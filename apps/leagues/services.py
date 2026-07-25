"""Syncing leagues, managers, and rosters from Sleeper.

The hard part is that a dynasty league gets a **new ``league_id`` every
season**. Sleeper links them with ``previous_league_id``; we walk that chain
backwards to collect every season of one dynasty, and fall back to matching the
league name when the chain is broken.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.utils.text import slugify

from apps.leagues.models import (
    League,
    LeagueSeason,
    Manager,
    RosterSlot,
    SleeperAccount,
    Team,
)
from apps.players.models import Player
from apps.players.services import upsert_players
from apps.sleeper.client import LeagueSource, SleeperAPIError, SleeperClient
from apps.sleeper.models import SyncRun

# A dynasty that has run for 25 seasons is not a thing; this only exists so a
# malformed previous_league_id can never spin forever.
MAX_CHAIN_DEPTH = 25

# Sleeper uses "0" as the placeholder for an empty starting slot.
EMPTY_SLOT = "0"

# Entries in roster_positions that are not part of the starting lineup.
NON_STARTING_POSITIONS = frozenset({"BN", "IR", "TAXI"})


def starting_slots(roster_positions: list[str]) -> list[str]:
    """The league's starting lineup slots, in order.

    Sleeper's ``starters`` array aligns index-for-index with this list, so it
    is what turns "a QB is starting" into "a QB is in the SUPER_FLEX".
    """
    return [p for p in roster_positions if p not in NON_STARTING_POSITIONS]


@dataclass(frozen=True)
class SlotAssignment:
    slot: str
    lineup_position: str = ""
    lineup_order: int | None = None


@dataclass
class LeagueSyncStats:
    leagues: int = 0
    seasons: int = 0
    teams: int = 0
    roster_slots: int = 0
    players_backfilled: int = 0
    league_names: list[str] = field(default_factory=list)

    @property
    def total_records(self) -> int:
        return self.seasons + self.teams + self.roster_slots


def normalize_league_name(name: str) -> str:
    """Casefold and strip everything but alphanumerics.

    So ``"The League"``, ``"the  league!"`` and ``"TheLeague"`` all collapse to
    the same key. This is the fallback used to tie seasons together when
    ``previous_league_id`` is missing.
    """
    return re.sub(r"[^a-z0-9]+", "", (name or "").casefold())


def _decimal_points(settings: dict[str, Any], whole: str, part: str) -> Decimal:
    """Sleeper splits points into an integer and a hundredths field."""
    units = settings.get(whole) or 0
    hundredths = settings.get(part) or 0
    try:
        return Decimal(str(units)) + (Decimal(str(hundredths)) / Decimal(100))
    except (TypeError, ValueError, InvalidOperation):
        # InvalidOperation is an ArithmeticError, not a ValueError — a
        # non-numeric string from Sleeper lands here and must not kill the sync.
        return Decimal(0)


def resolve_account(client: LeagueSource, username: str) -> SleeperAccount:
    """Look up my Sleeper user and cache it. Username may have changed."""
    user = client.get_user(username)
    account, _ = SleeperAccount.objects.update_or_create(
        sleeper_user_id=str(user["user_id"]),
        defaults={
            "username": user.get("username") or username,
            "display_name": user.get("display_name") or "",
            "avatar": user.get("avatar") or "",
        },
    )
    return account


def walk_season_chain(
    client: LeagueSource, league: dict[str, Any]
) -> list[dict[str, Any]]:
    """Follow ``previous_league_id`` back through every prior season.

    Returns newest-first. Guarded against cycles and self-references, which
    would otherwise hang the sync.
    """
    chain = [league]
    seen = {str(league.get("league_id"))}
    current = league

    while len(chain) < MAX_CHAIN_DEPTH:
        previous_id = current.get("previous_league_id")
        if not previous_id or str(previous_id) in seen:
            break
        previous = client.get_league(str(previous_id))
        if not previous:
            break
        seen.add(str(previous_id))
        chain.append(previous)
        current = previous

    return chain


def bind_chain_to_league(chain: list[dict[str, Any]]) -> tuple[League, bool]:
    """Find or create the ``League`` this chain of seasons belongs to.

    Precedence: an already-known ``sleeper_league_id`` anywhere in the chain,
    then a normalized-name match, then a new League.
    """
    league_ids = [str(entry.get("league_id")) for entry in chain]
    known = (
        LeagueSeason.objects.filter(sleeper_league_id__in=league_ids)
        .select_related("league")
        .first()
    )
    if known is not None:
        return known.league, False

    name = chain[0].get("name") or "Unnamed league"
    normalized = normalize_league_name(name)

    by_name = League.objects.filter(normalized_name=normalized).first()
    if by_name is not None:
        return by_name, False

    return (
        League.objects.create(
            name=name,
            normalized_name=normalized,
            slug=_unique_slug(name),
        ),
        True,
    )


def _unique_slug(name: str) -> str:
    base = slugify(name) or "league"
    slug = base
    suffix = 2
    while League.objects.filter(slug=slug).exists():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def upsert_league_season(league: League, payload: dict[str, Any]) -> LeagueSeason:
    season, _ = LeagueSeason.objects.update_or_create(
        sleeper_league_id=str(payload.get("league_id")),
        defaults={
            "league": league,
            "season": str(payload.get("season") or ""),
            "previous_sleeper_league_id": str(payload.get("previous_league_id") or ""),
            "status": payload.get("status") or "",
            "total_rosters": payload.get("total_rosters") or 0,
            "roster_positions": payload.get("roster_positions") or [],
            "scoring_settings": payload.get("scoring_settings") or {},
            "settings": payload.get("settings") or {},
        },
    )
    return season


def ensure_players_exist(client: LeagueSource, sleeper_ids: set[str]) -> int:
    """Guarantee a ``Player`` row exists for every id, whatever the filter says.

    ``sync_players`` deliberately keeps only live NFL players, but a rival can
    roster an unsigned veteran or a UDFA whose ``team`` is null. Those would be
    missing, and ``RosterSlot`` creation would then fail on the foreign key.
    So: fetch the dump once (only if there's actually a gap) and insert the
    stragglers, bypassing the filter. Anything Sleeper itself doesn't know gets
    a stub row so the roster still syncs.
    """
    sleeper_ids = {sid for sid in sleeper_ids if sid}
    if not sleeper_ids:
        return 0

    existing = set(
        Player.objects.filter(sleeper_id__in=sleeper_ids).values_list(
            "sleeper_id", flat=True
        )
    )
    missing = sleeper_ids - existing
    if not missing:
        return 0

    dump = client.get_all_players() or {}
    payloads = [dump[sid] for sid in missing if sid in dump]
    upsert_players(payloads)

    unknown = missing - {str(p.get("player_id")) for p in payloads}
    if unknown:
        Player.objects.bulk_create(
            [
                Player(sleeper_id=sid, full_name=f"Unknown player {sid}")
                for sid in sorted(unknown)
            ],
            ignore_conflicts=True,
        )

    return len(missing)


def _slot_map(
    roster: dict[str, Any], roster_positions: list[str] | None = None
) -> dict[str, SlotAssignment]:
    """Assign each rostered player a slot.

    Priority matters: a player listed in both ``starters`` and ``players``
    is a starter, not a bench player — so starters are applied last.
    """
    slots: dict[str, SlotAssignment] = {}

    for sleeper_id in roster.get("players") or []:
        if sleeper_id and sleeper_id != EMPTY_SLOT:
            slots[str(sleeper_id)] = SlotAssignment(RosterSlot.Slot.BENCH)

    for key, slot in (
        ("reserve", RosterSlot.Slot.IR),
        ("taxi", RosterSlot.Slot.TAXI),
    ):
        for sleeper_id in roster.get(key) or []:
            if sleeper_id and sleeper_id != EMPTY_SLOT:
                slots[str(sleeper_id)] = SlotAssignment(slot)

    lineup = starting_slots(roster_positions or [])
    for index, sleeper_id in enumerate(roster.get("starters") or []):
        # "0" means the manager left that slot empty — no player to record.
        if not sleeper_id or sleeper_id == EMPTY_SLOT:
            continue
        slots[str(sleeper_id)] = SlotAssignment(
            RosterSlot.Slot.STARTER,
            lineup[index] if index < len(lineup) else "",
            index,
        )

    return slots


def sync_league_season(
    client: LeagueSource,
    season: LeagueSeason,
    account: SleeperAccount | None,
    stats: LeagueSyncStats,
) -> None:
    """Pull managers, teams, and rosters for one season."""
    users = client.get_league_users(season.sleeper_league_id)
    rosters = client.get_league_rosters(season.sleeper_league_id)

    managers: dict[str, Manager] = {}
    team_names: dict[str, str] = {}
    for user in users:
        user_id = str(user.get("user_id"))
        manager, _ = Manager.objects.update_or_create(
            sleeper_user_id=user_id,
            defaults={
                "username": user.get("username") or "",
                "display_name": user.get("display_name") or "",
                "avatar": user.get("avatar") or "",
                "is_me": bool(account and user_id == account.sleeper_user_id),
            },
        )
        managers[user_id] = manager
        team_names[user_id] = (user.get("metadata") or {}).get("team_name") or ""

    referenced: set[str] = set()
    for roster in rosters:
        referenced.update(_slot_map(roster, season.roster_positions))
    stats.players_backfilled += ensure_players_exist(client, referenced)

    players_by_id = {
        p.sleeper_id: p for p in Player.objects.filter(sleeper_id__in=referenced)
    }

    for roster in rosters:
        owner_id = str(roster.get("owner_id") or "")
        owner = managers.get(owner_id)
        roster_settings = roster.get("settings") or {}

        team, _ = Team.objects.update_or_create(
            league_season=season,
            roster_id=int(roster.get("roster_id") or 0),
            defaults={
                "manager": owner,
                "team_name": team_names.get(owner_id, ""),
                "wins": roster_settings.get("wins") or 0,
                "losses": roster_settings.get("losses") or 0,
                "ties": roster_settings.get("ties") or 0,
                "points_for": _decimal_points(roster_settings, "fpts", "fpts_decimal"),
                "points_against": _decimal_points(
                    roster_settings, "fpts_against", "fpts_against_decimal"
                ),
                "waiver_budget_used": roster_settings.get("waiver_budget_used") or 0,
            },
        )
        stats.teams += 1

        # Rebuild rather than diff — rosters churn constantly and the table is
        # tiny, so a clean rewrite is simpler and can't leave stale rows.
        team.roster_slots.all().delete()
        slots = [
            RosterSlot(
                team=team,
                player=players_by_id[sleeper_id],
                slot=assignment.slot,
                lineup_position=assignment.lineup_position,
                lineup_order=assignment.lineup_order,
            )
            for sleeper_id, assignment in _slot_map(
                roster, season.roster_positions
            ).items()
            if sleeper_id in players_by_id
        ]
        RosterSlot.objects.bulk_create(slots)
        stats.roster_slots += len(slots)


def sync_leagues(
    client: LeagueSource | None = None,
    *,
    username: str = "",
    season: str = "",
) -> LeagueSyncStats:
    """Sync every league the account plays in, plus all their prior seasons.

    The ``SyncRun`` bookkeeping deliberately sits *outside* the transaction:
    if the write below rolls back, we still want the failure on record.
    """
    client = client or SleeperClient()
    stats = LeagueSyncStats()

    with SyncRun.track(SyncRun.Kind.LEAGUE) as run:
        _sync_leagues(client, username, season, stats)
        run.records_written = stats.total_records

    return stats


@transaction.atomic
def _sync_leagues(
    client: LeagueSource,
    username: str,
    season: str,
    stats: LeagueSyncStats,
) -> None:
    """The actual work — all-or-nothing, so a mid-sync failure leaves no half-league."""
    if not username:
        raise SleeperAPIError(
            "No Sleeper username configured. Set SLEEPER_USERNAME in .env "
            "or pass --username."
        )

    account = resolve_account(client, username)
    target_season = season or str(client.get_nfl_state().get("season") or "")

    for entry in client.get_user_leagues(account.sleeper_user_id, target_season):
        chain = walk_season_chain(client, entry)
        league, _created = bind_chain_to_league(chain)
        if league.name not in stats.league_names:
            stats.league_names.append(league.name)
            stats.leagues += 1

        for payload in chain:
            league_season = upsert_league_season(league, payload)
            stats.seasons += 1
            sync_league_season(client, league_season, account, stats)
