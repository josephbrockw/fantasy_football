"""Turning Sleeper's player dump into ``Player`` rows.

Sleeper returns ~12,200 players; only ~1,043 are live NFL fantasy assets. The
filter here does that reduction, and the normalisers below repair the several
places where Sleeper's payload is not directly usable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

from apps.players.models import Player, PlayerWeekStat, TrendingPlayer
from apps.sleeper.client import (
    PlayerSource,
    SleeperClient,
    StatsSource,
    TrendingSource,
)
from apps.sleeper.models import SyncRun

FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})

# Sleeper parks unranked players on these values rather than using null.
# 1,436 players share 9999999 and 517 share 999.
SEARCH_RANK_SENTINELS = frozenset({999, 9999999})

BATCH_SIZE = 500

# Everything except the natural key and created-once bookkeeping.
UPDATE_FIELDS = [
    "first_name",
    "last_name",
    "full_name",
    "search_full_name",
    "position",
    "fantasy_positions",
    "team",
    "number",
    "status",
    "active",
    "age",
    "birth_date",
    "years_exp",
    "rookie_year",
    "college",
    "height",
    "weight",
    "depth_chart_position",
    "depth_chart_order",
    "injury_status",
    "injury_body_part",
    "search_rank",
    "raw",
    "synced_at",
]


@dataclass
class SyncStats:
    written: int = 0
    skipped: int = 0


def is_live_player(payload: dict[str, Any]) -> bool:
    """Is this a real, currently-rostered NFL player in a fantasy position?

    Deliberately keys off ``team``, not ``active``. Sleeper reports Tom Brady,
    Drew Brees, Antonio Brown, Todd Gurley and Ezekiel Elliott as
    ``active: true`` with ``status: "Active"`` years after they retired; the
    only field that reliably goes empty is ``team``.
    """
    return bool(payload.get("team")) and payload.get("position") in FANTASY_POSITIONS


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    # The model uses PositiveIntegerField throughout; negatives are bad data.
    return parsed if parsed >= 0 else None


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _search_rank(value: Any) -> int | None:
    rank = _as_int(value)
    if rank is None or rank in SEARCH_RANK_SENTINELS:
        return None
    return rank


def _full_name(payload: dict[str, Any]) -> str:
    """Compose a display name.

    Team defenses carry no ``full_name`` — the HOU record is
    ``first_name: "Houston", last_name: "Texans"`` — so fall back to joining
    the parts.
    """
    name = _as_str(payload.get("full_name")).strip()
    if name:
        return name
    parts = [
        _as_str(payload.get("first_name")).strip(),
        _as_str(payload.get("last_name")).strip(),
    ]
    return " ".join(part for part in parts if part)


def player_from_payload(payload: dict[str, Any]) -> Player:
    """Build an unsaved ``Player`` from one Sleeper record."""
    metadata = payload.get("metadata") or {}
    fantasy_positions = payload.get("fantasy_positions") or []

    return Player(
        sleeper_id=_as_str(payload.get("player_id")),
        first_name=_as_str(payload.get("first_name")),
        last_name=_as_str(payload.get("last_name")),
        full_name=_full_name(payload),
        search_full_name=_as_str(payload.get("search_full_name")),
        position=_as_str(payload.get("position")),
        fantasy_positions=list(fantasy_positions),
        team=_as_str(payload.get("team")),
        number=_as_int(payload.get("number")),
        status=_as_str(payload.get("status")),
        active=bool(payload.get("active")),
        age=_as_int(payload.get("age")),
        birth_date=_as_date(payload.get("birth_date")),
        years_exp=_as_int(payload.get("years_exp")),
        rookie_year=_as_int(metadata.get("rookie_year")),
        college=_as_str(payload.get("college")),
        height=_as_str(payload.get("height")),
        weight=_as_str(payload.get("weight")),
        depth_chart_position=_as_str(payload.get("depth_chart_position")),
        depth_chart_order=_as_int(payload.get("depth_chart_order")),
        injury_status=_as_str(payload.get("injury_status")),
        injury_body_part=_as_str(payload.get("injury_body_part")),
        search_rank=_search_rank(payload.get("search_rank")),
        raw=payload,
    )


def upsert_players(payloads: list[dict[str, Any]]) -> int:
    """Insert-or-update players by ``sleeper_id``. Returns the count written."""
    if not payloads:
        return 0
    instances = [player_from_payload(p) for p in payloads if p.get("player_id")]
    Player.objects.bulk_create(
        instances,
        batch_size=BATCH_SIZE,
        update_conflicts=True,
        unique_fields=["sleeper_id"],
        update_fields=UPDATE_FIELDS,
    )
    return len(instances)


def sync_players(
    client: PlayerSource | None = None,
    *,
    include_inactive: bool = False,
    dry_run: bool = False,
) -> SyncStats:
    """Fetch the Sleeper player dump and store the live players.

    ``include_inactive`` bypasses the filter and stores every record, as a
    backfill escape hatch.
    """
    client = client or SleeperClient()
    stats = SyncStats()

    with SyncRun.track(SyncRun.Kind.PLAYERS) as run:
        dump = client.get_all_players() or {}

        keep: list[dict[str, Any]] = []
        for payload in dump.values():
            if include_inactive or is_live_player(payload):
                keep.append(payload)
            else:
                stats.skipped += 1

        stats.written = len(keep) if dry_run else upsert_players(keep)

        run.records_written = stats.written
        run.records_skipped = stats.skipped

    return stats


# Earliest season worth backfilling. Sleeper has older data, but the tracked
# player universe and ML relevance start here; override with --start-season.
MIN_SEASON = 2018

STAT_UPDATE_FIELDS = ["pts_ppr", "pts_half_ppr", "pts_std", "stats", "updated_at"]


def stat_row_from_payload(
    player: Player,
    season: int,
    week: int,
    season_type: str,
    kind: str,
    stat_dict: dict[str, Any],
) -> PlayerWeekStat:
    """Build one unsaved ``PlayerWeekStat`` row.

    ``updated_at`` is set explicitly because ``bulk_create(update_conflicts=True)``
    bypasses ``TimeStampedModel``'s ``auto_now`` — the same caveat ``sync_players``
    works around.
    """
    return PlayerWeekStat(
        player=player,
        season=season,
        week=week,
        season_type=season_type,
        kind=kind,
        pts_ppr=_as_float(stat_dict.get("pts_ppr")),
        pts_half_ppr=_as_float(stat_dict.get("pts_half_ppr")),
        pts_std=_as_float(stat_dict.get("pts_std")),
        stats=stat_dict,
        updated_at=timezone.now(),
    )


def upsert_week_stats(rows: list[PlayerWeekStat]) -> int:
    """Insert-or-update stat rows on their natural key. Returns count written."""
    if not rows:
        return 0
    PlayerWeekStat.objects.bulk_create(
        rows,
        batch_size=BATCH_SIZE,
        update_conflicts=True,
        unique_fields=["player", "season", "week", "season_type", "kind"],
        update_fields=STAT_UPDATE_FIELDS,
    )
    return len(rows)


def ingest_week(
    client: StatsSource,
    season: int,
    week: int,
    kind: str,
    season_type: str = "regular",
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Fetch one (season, week, kind) and store rows for known players only.

    The payload is keyed by the whole Sleeper universe (~550 KB); like
    ``sync_trending`` we resolve the ids we track in one query and count the rest
    as skipped, keeping stored volume bounded. Returns ``(written, skipped)``.
    """
    fetch = (
        client.get_player_stats
        if kind == PlayerWeekStat.Kind.STAT
        else client.get_player_projections
    )
    payload = fetch(str(season), week, season_type) or {}
    if not payload:  # an empty week is normal, not an error
        return 0, 0

    ids = [str(pid) for pid in payload]
    known = {
        player.sleeper_id: player
        for player in Player.objects.filter(sleeper_id__in=ids)
    }
    rows: list[PlayerWeekStat] = []
    skipped = 0
    for player_id, stat_dict in payload.items():
        player = known.get(str(player_id))
        if player is None:
            skipped += 1
            continue
        rows.append(
            stat_row_from_payload(
                player, season, week, season_type, kind, stat_dict or {}
            )
        )

    written = len(rows) if dry_run else upsert_week_stats(rows)
    return written, skipped


def sync_stats(
    client: StatsSource | None = None,
    *,
    start_season: int | None = None,
    end_season: int | None = None,
    weeks: Iterable[int] | None = None,
    kinds: Sequence[str] = (PlayerWeekStat.Kind.STAT, PlayerWeekStat.Kind.PROJECTION),
    season_type: str = "regular",
    dry_run: bool = False,
) -> SyncStats:
    """Backfill weekly stats and projections across a season/week range.

    With no bounds it pulls ``MIN_SEASON`` through the current season (from
    ``get_nfl_state``), weeks 1–18, for both kinds. Idempotent — each week upserts
    on the natural key, so re-running a range refreshes in place.
    """
    client = client or SleeperClient()
    stats = SyncStats()
    start = start_season or MIN_SEASON
    week_list = list(weeks) if weeks is not None else list(range(1, 19))

    with SyncRun.track(SyncRun.Kind.STATS) as run:
        end = end_season or int(client.get_nfl_state().get("season") or MIN_SEASON)
        for season in range(start, end + 1):
            for week in week_list:
                for kind in kinds:
                    written, skipped = ingest_week(
                        client, season, week, kind, season_type, dry_run=dry_run
                    )
                    stats.written += written
                    stats.skipped += skipped
        run.records_written = stats.written
        run.records_skipped = stats.skipped

    return stats


def sync_trending(
    client: TrendingSource | None = None,
    *,
    lookback_hours: int = 24,
    limit: int = 100,
) -> SyncStats:
    """Refresh the trending add/drop counts.

    The endpoint returns only ``{player_id, count}``, and it covers all of
    Sleeper — including players our filter never stored. Unknown ids are
    skipped rather than treated as an error.
    """
    client = client or SleeperClient()
    stats = SyncStats()

    with SyncRun.track(SyncRun.Kind.TRENDING) as run:
        rows: list[TrendingPlayer] = []
        for kind in (TrendingPlayer.Kind.ADD, TrendingPlayer.Kind.DROP):
            entries = client.get_trending_players(
                kind=kind, lookback_hours=lookback_hours, limit=limit
            )
            ids = [str(entry.get("player_id")) for entry in entries]
            known = {
                player.sleeper_id: player
                for player in Player.objects.filter(sleeper_id__in=ids)
            }
            for entry in entries:
                player = known.get(str(entry.get("player_id")))
                if player is None:
                    stats.skipped += 1
                    continue
                rows.append(
                    TrendingPlayer(
                        player=player,
                        kind=kind,
                        count=entry.get("count") or 0,
                        lookback_hours=lookback_hours,
                    )
                )

        # Rolling window, not history — replace wholesale.
        TrendingPlayer.objects.all().delete()
        TrendingPlayer.objects.bulk_create(rows, batch_size=BATCH_SIZE)
        stats.written = len(rows)

        run.records_written = stats.written
        run.records_skipped = stats.skipped

    return stats
