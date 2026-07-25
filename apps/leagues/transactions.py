"""Syncing trades and traded draft picks from Sleeper.

Only **completed trades** (``type == "trade"``, ``status == "complete"``) are
ingested; waiver / free-agent / commissioner transactions arrive on the same
endpoint and are deliberately skipped (that's the separate "Waiver/FAAB"
feature). Traded-pick ownership is a current-state snapshot, rebuilt wholesale
each run — mirroring how ``RosterSlot`` is replaced rather than diffed.

Requires a prior ``sync_league``: trades and picks reference ``roster_id``s that
this maps to the season's ``Team``s (and thus, for picks, to cross-season-stable
``Manager``s).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.db import transaction

from apps.leagues.models import LeagueSeason, Team, Trade, TradeAsset, TradedPick
from apps.players.models import Player
from apps.sleeper.client import SleeperClient, TransactionSource
from apps.sleeper.models import SyncRun

# Regular season plus playoff weeks; offseason trades land on the earliest legs.
MAX_WEEK = 18


@dataclass
class TransactionSyncStats:
    trades: int = 0
    assets: int = 0
    picks: int = 0
    skipped: int = 0

    @property
    def total_records(self) -> int:
        return self.trades + self.assets + self.picks


def _ms_to_datetime(value: Any) -> datetime | None:
    """Sleeper's ``status_updated`` is epoch **milliseconds**."""
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _team_for(teams_by_roster: dict[int, Team], roster_id: Any) -> Team | None:
    """The ``Team`` for a roster id (which arrives untyped from JSON)."""
    return teams_by_roster.get(roster_id)


def _manager_for(teams_by_roster: dict[int, Team], roster_id: Any) -> Any:
    """The ``Manager`` owning a roster, or ``None`` for an orphan/unknown one."""
    team = _team_for(teams_by_roster, roster_id)
    return team.manager if team is not None else None


def sync_transactions(
    client: TransactionSource | None = None, *, season: str = ""
) -> TransactionSyncStats:
    """Ingest trades and traded picks for already-synced league seasons.

    ``SyncRun`` bookkeeping sits outside the per-season transactions, exactly as
    ``sync_leagues`` does — a failure still lands on record.
    """
    client = client or SleeperClient()
    stats = TransactionSyncStats()

    with SyncRun.track(SyncRun.Kind.TRANSACTIONS) as run:
        seasons = LeagueSeason.objects.select_related("league")
        if season:
            seasons = seasons.filter(season=season)
        for league_season in seasons:
            _sync_season(client, league_season, stats)
        run.records_written = stats.total_records
        run.records_skipped = stats.skipped

    return stats


@transaction.atomic
def _sync_season(
    client: TransactionSource,
    season: LeagueSeason,
    stats: TransactionSyncStats,
) -> None:
    """One season, atomic so a malformed season can't roll back the others."""
    teams_by_roster = {
        team.roster_id: team for team in season.teams.select_related("manager")
    }
    _sync_trades(client, season, teams_by_roster, stats)
    _sync_traded_picks(client, season, teams_by_roster, stats)


def _sync_trades(
    client: TransactionSource,
    season: LeagueSeason,
    teams_by_roster: dict[int, Team],
    stats: TransactionSyncStats,
) -> None:
    for week in range(1, MAX_WEEK + 1):
        for txn in client.get_league_transactions(season.sleeper_league_id, week):
            # Only completed trades. Dropped/vetoed offers are trades too, but
            # with a non-complete status; other types are waivers / FA moves.
            if txn.get("type") != "trade" or txn.get("status") != "complete":
                continue
            trade, _ = Trade.objects.update_or_create(
                sleeper_transaction_id=str(txn.get("transaction_id")),
                defaults={
                    "league_season": season,
                    "week": txn.get("leg") or week,
                    "status": txn.get("status") or "",
                    "status_updated": _ms_to_datetime(txn.get("status_updated")),
                },
            )
            # Rebuild the assets — cheap, and can't leave stale rows.
            trade.assets.all().delete()
            assets = _build_assets(trade, txn, teams_by_roster, stats)
            TradeAsset.objects.bulk_create(assets)
            stats.trades += 1
            stats.assets += len(assets)


def _build_assets(
    trade: Trade,
    txn: dict[str, Any],
    teams_by_roster: dict[int, Team],
    stats: TransactionSyncStats,
) -> list[TradeAsset]:
    assets: list[TradeAsset] = []

    # Players: `adds` maps player_id → receiving roster, `drops` → sending roster.
    adds = txn.get("adds") or {}
    drops = txn.get("drops") or {}
    players_by_id = {
        p.sleeper_id: p
        for p in Player.objects.filter(sleeper_id__in=[str(pid) for pid in adds])
    }
    for player_id, to_roster in adds.items():
        player = players_by_id.get(str(player_id))
        if player is None:
            # A traded player should already exist (recently rostered); if not,
            # skip the one asset rather than backfill here.
            stats.skipped += 1
            continue
        assets.append(
            TradeAsset(
                trade=trade,
                kind=TradeAsset.Kind.PLAYER,
                player=player,
                from_team=_team_for(teams_by_roster, drops.get(player_id)),
                to_team=_team_for(teams_by_roster, to_roster),
            )
        )

    # Picks: draft_picks carries the pick's season/round and sender/receiver.
    for pick in txn.get("draft_picks") or []:
        assets.append(
            TradeAsset(
                trade=trade,
                kind=TradeAsset.Kind.PICK,
                pick_season=str(pick.get("season") or ""),
                pick_round=pick.get("round"),
                from_team=_team_for(teams_by_roster, pick.get("previous_owner_id")),
                to_team=_team_for(teams_by_roster, pick.get("owner_id")),
            )
        )

    # FAAB: waiver_budget carries sender/receiver roster ids and an amount.
    for budget in txn.get("waiver_budget") or []:
        assets.append(
            TradeAsset(
                trade=trade,
                kind=TradeAsset.Kind.FAAB,
                faab_amount=budget.get("amount"),
                from_team=_team_for(teams_by_roster, budget.get("sender")),
                to_team=_team_for(teams_by_roster, budget.get("receiver")),
            )
        )

    return assets


def _sync_traded_picks(
    client: TransactionSource,
    season: LeagueSeason,
    teams_by_roster: dict[int, Team],
    stats: TransactionSyncStats,
) -> None:
    # Current state — rebuild wholesale, like the roster rebuild.
    TradedPick.objects.filter(league_season=season).delete()
    picks: list[TradedPick] = []
    for pick in client.get_traded_picks(season.sleeper_league_id):
        original_owner = _manager_for(teams_by_roster, pick.get("roster_id"))
        current_owner = _manager_for(teams_by_roster, pick.get("owner_id"))
        if original_owner is None or current_owner is None:
            # An orphan/renumbered roster: better a missing row than a wrong owner.
            stats.skipped += 1
            continue
        picks.append(
            TradedPick(
                league_season=season,
                season=str(pick.get("season") or ""),
                round=pick["round"],
                original_owner=original_owner,
                current_owner=current_owner,
            )
        )
    TradedPick.objects.bulk_create(picks)
    stats.picks += len(picks)
