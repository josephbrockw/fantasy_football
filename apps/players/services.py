"""Turning Sleeper's player dump into ``Player`` rows.

Sleeper returns ~12,200 players; only ~1,043 are live NFL fantasy assets. The
filter here does that reduction, and the normalisers below repair the several
places where Sleeper's payload is not directly usable.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import groupby
from typing import Any

from django.utils import timezone

from apps.players.models import (
    Player,
    PlayerSeasonMetrics,
    PlayerWeekStat,
    TrendingPlayer,
)
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


# --- Season metrics (feature 005) --------------------------------------------
# Deterministic feature engineering over the ingested stats — no network, no ML.

# Trailing played weeks used for the recent-form average.
RECENT_WINDOW = 4

# Sleeper usage stat keys promoted to columns.
TARGETS_KEY = "rec_tgt"
CARRIES_KEY = "rush_att"
SNAPS_KEY = "off_snp"

METRICS_UPDATE_FIELDS = [
    "position",
    "games_played",
    "total_ppr",
    "total_half_ppr",
    "total_std",
    "ppg_ppr",
    "ppg_half_ppr",
    "ppg_std",
    "stdev_ppr",
    "floor_ppr",
    "ceiling_ppr",
    "recent_ppg_ppr",
    "form_delta_ppr",
    "targets",
    "carries",
    "snaps",
    "usage",
    "updated_at",
]


def _summed_usage(rows: list[PlayerWeekStat]) -> dict[str, float]:
    """Sum every numeric non-scoring stat key across the played weeks."""
    usage: dict[str, float] = {}
    for row in rows:
        for key, value in (row.stats or {}).items():
            if key.startswith("pts_"):  # scoring totals are promoted elsewhere
                continue
            number = _as_float(value)
            if number is None:
                continue
            usage[key] = usage.get(key, 0.0) + number
    return usage


def _usage_count(usage: dict[str, float], key: str) -> int | None:
    return int(usage[key]) if key in usage else None


def metrics_from_week_rows(
    player: Player,
    season: int,
    season_type: str,
    rows: list[PlayerWeekStat],
) -> PlayerSeasonMetrics:
    """Aggregate one player-season's weekly stat rows into a metrics row.

    "Played" means a non-null ``pts_ppr`` (Sleeper nulls fantasy points for a
    week not played; counting those would deflate per-game averages). Every ratio
    is guarded, so a zero-played-weeks player yields nulls rather than a
    ``ZeroDivisionError``. Recent form is within-season only — a season-spanning
    window is a future refinement.
    """
    played = [row for row in rows if row.pts_ppr is not None]
    games = len(played)
    metrics = PlayerSeasonMetrics(
        player=player,
        season=season,
        season_type=season_type,
        position=player.position,
        games_played=games,
        updated_at=timezone.now(),
    )
    if not played:
        return metrics

    # The `is not None` filter is redundant (played already excludes them) but
    # narrows the list to float for the numeric aggregations below.
    ppr = [row.pts_ppr for row in played if row.pts_ppr is not None]
    metrics.total_ppr = sum(ppr)
    metrics.total_half_ppr = sum(row.pts_half_ppr or 0.0 for row in played)
    metrics.total_std = sum(row.pts_std or 0.0 for row in played)
    metrics.ppg_ppr = metrics.total_ppr / games
    metrics.ppg_half_ppr = metrics.total_half_ppr / games
    metrics.ppg_std = metrics.total_std / games

    metrics.stdev_ppr = statistics.pstdev(ppr)
    metrics.floor_ppr = min(ppr)
    metrics.ceiling_ppr = max(ppr)

    recent = ppr[-RECENT_WINDOW:]
    metrics.recent_ppg_ppr = sum(recent) / len(recent)
    metrics.form_delta_ppr = metrics.recent_ppg_ppr - metrics.ppg_ppr

    usage = _summed_usage(played)
    metrics.usage = usage
    metrics.targets = _usage_count(usage, TARGETS_KEY)
    metrics.carries = _usage_count(usage, CARRIES_KEY)
    metrics.snaps = _usage_count(usage, SNAPS_KEY)
    return metrics


def upsert_metrics(rows: list[PlayerSeasonMetrics]) -> int:
    """Insert-or-update metrics on the natural key. Returns count written."""
    if not rows:
        return 0
    PlayerSeasonMetrics.objects.bulk_create(
        rows,
        batch_size=BATCH_SIZE,
        update_conflicts=True,
        unique_fields=["player", "season", "season_type"],
        update_fields=METRICS_UPDATE_FIELDS,
    )
    return len(rows)


def recompute_season(
    season: int, season_type: str = "regular", *, dry_run: bool = False
) -> list[PlayerSeasonMetrics]:
    """Build (and, unless ``dry_run``, upsert) metrics for a whole season."""
    week_rows = (
        PlayerWeekStat.objects.filter(
            season=season, season_type=season_type, kind=PlayerWeekStat.Kind.STAT
        )
        .select_related("player")
        .order_by("player_id", "week")
    )
    metrics: list[PlayerSeasonMetrics] = []
    for _player_id, group in groupby(week_rows, key=lambda r: r.player_id):
        group_rows = list(group)
        metrics.append(
            metrics_from_week_rows(
                group_rows[0].player, season, season_type, group_rows
            )
        )
    if not dry_run:
        upsert_metrics(metrics)
    return metrics


def recompute_metrics(
    *,
    seasons: Iterable[int] | None = None,
    season_type: str = "regular",
    dry_run: bool = False,
) -> SyncStats:
    """Rebuild ``PlayerSeasonMetrics`` from ingested stats — DB only, no network.

    Defaults to every season present in ``PlayerWeekStat``. Idempotent: each
    season upserts on ``(player, season, season_type)``. Wrapped in a ``SyncRun``
    (``metrics`` kind) for audit parity with the sync commands.
    """
    stats = SyncStats()
    with SyncRun.track(SyncRun.Kind.METRICS) as run:
        if seasons is None:
            seasons = list(
                PlayerWeekStat.objects.values_list("season", flat=True).distinct()
            )
        for season in seasons:
            built = recompute_season(season, season_type, dry_run=dry_run)
            stats.written += len(built)
        run.records_written = stats.written
        run.records_skipped = stats.skipped
    return stats
