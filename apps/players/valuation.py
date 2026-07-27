"""``baseline-v1`` dynasty valuation — a transparent, deterministic three-axis model.

Every player in the scoring pool gets three sub-scores, each normalized 0–100
across the pool and stored as a column so weight profiles can re-blend them at
read time (feature 006 PR 03):

- ``now_score``     — this-season lineup value: production (005 metrics) × a
                       clamped durability factor. No age adjustment.
- ``prospect_score``— breakout likelihood: a draft-capital pedigree prior (011)
                       decayed by accumulated evidence, plus usage-trajectory,
                       depth-chart and market nudges.
- ``horizon_score`` — how long current form holds: a position age curve.

``value`` is the ``DEFAULT_PROFILE`` blend, stored as the default sort key. Every
intermediate is recorded in ``components`` so a surprising number is debuggable.
The compute reads only the DB — no network. A trained model can register in
``VALUATION_MODELS`` and be activated by ``ACTIVE_MODEL_VERSION`` without touching
the command, model, or templates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from django.db.models import (
    ExpressionWrapper,
    FloatField,
    Max,
    OuterRef,
    QuerySet,
    Subquery,
    Sum,
)
from django.utils import timezone

from apps.enrichment.models import PlayerProfile
from apps.players.models import (
    Player,
    PlayerSeasonMetrics,
    PlayerValue,
    TrendingPlayer,
)
from apps.players.services import BATCH_SIZE, SyncStats
from apps.sleeper.models import SyncRun

ACTIVE_MODEL_VERSION = "baseline-v1"

# Blend weights per stance, each summing to 1.0. Starting points to tune, not
# gospel. The stored `value` uses DEFAULT_PROFILE; PR 03 re-blends the rest.
WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "balanced": {"now": 0.45, "prospect": 0.35, "horizon": 0.20},
    "contend": {"now": 0.70, "prospect": 0.15, "horizon": 0.15},
    "rebuild": {"now": 0.20, "prospect": 0.55, "horizon": 0.25},
}
DEFAULT_PROFILE = "contend"

# Position age curves: (peak_age, max_horizon_seasons, decline_per_year_past_peak).
# RB falls off a cliff early; WR/TE flatter; QB longest.
AGE_CURVES: dict[str, tuple[int, float, float]] = {
    "RB": (24, 5.0, 1.0),
    "WR": (26, 7.0, 0.6),
    "TE": (26, 7.0, 0.6),
    "QB": (28, 10.0, 0.5),
    "K": (30, 8.0, 0.4),
    "DEF": (30, 8.0, 0.4),
}
DEFAULT_AGE_CURVE = (26, 6.0, 0.7)

# Draft-capital pedigree prior by round; round 1 split by overall pick.
ROUND1_EARLY = 1.1  # picks 1–16
ROUND1_LATE = 0.9
PEDIGREE_PRIORS: dict[int, float] = {2: 0.7, 3: 0.5, 4: 0.35, 5: 0.25, 6: 0.18, 7: 0.12}
UNDRAFTED_PRIOR = 0.1

# The scale (in "evidence units": career games + years of experience) at which
# the pedigree prior has decayed to half. ~2 seasons of full evidence.
EVIDENCE_HALF_LIFE = 34.0

PROSPECT_WINDOW = 3  # draft classes (years) included in the pool
DURABILITY_WINDOW = 3  # seasons of games-played history for durability
DURABILITY_FLOOR = 0.75  # never zero a producer for availability
GAMES_PER_SEASON = 17

PRODUCTION_RECENT_WEIGHT = 0.3  # how much recent form tilts the production blend
TRAJECTORY_NUDGE_CAP = 0.15
TRAJECTORY_SCALE = 10.0  # ppg form-delta that maps to the full cap
DEPTH_CHART_NUDGE_CAP = 0.15
MARKET_NUDGE_CAP = 0.1
MARKET_SCALE = 50000.0  # trending add-count that maps to the full cap

# Positional tiers by percentile *within a position* (1 = elite). A player is
# Tier 1 if he's in the top 5% of his position by value, Tier 2 in the top 15%,
# and so on; anyone past the last cutoff falls into the overflow tier. Percentile
# rather than absolute value so "Tier 1 RB" means top-of-position, not a raw
# score that a weak position might never reach.
TIER_PERCENTILES = [0.05, 0.15, 0.30, 0.50, 0.75]


# --- axis factor functions (each tiny + unit-tested) -------------------------


def raw_production(metrics: PlayerSeasonMetrics | None) -> float:
    """Season ppg nudged toward recent form; 0.0 when the player never played."""
    if metrics is None or metrics.ppg_ppr is None:
        return 0.0
    ppg = metrics.ppg_ppr
    recent = metrics.recent_ppg_ppr if metrics.recent_ppg_ppr is not None else ppg
    return (1 - PRODUCTION_RECENT_WEIGHT) * ppg + PRODUCTION_RECENT_WEIGHT * recent


def durability_factor(recent_metrics: list[PlayerSeasonMetrics]) -> float:
    """Games-played rate over the last DURABILITY_WINDOW seasons.

    Clamped to [DURABILITY_FLOOR, 1.0]; 1.0 with no history (a rookie is not
    injury-prone by default). Multiplies production — availability is part of
    this-year lineup value.
    """
    if not recent_metrics:
        return 1.0
    played = sum(m.games_played for m in recent_metrics)
    possible = len(recent_metrics) * GAMES_PER_SEASON  # non-empty → always > 0
    return max(DURABILITY_FLOOR, min(1.0, played / possible))


def pedigree_prior(profile: PlayerProfile | None) -> float:
    """Draft-capital prior: round 1 high, by-round decline, undrafted floor."""
    if profile is None or profile.draft_round is None:
        return UNDRAFTED_PRIOR
    if profile.draft_round == 1:
        pick = profile.draft_pick
        return ROUND1_EARLY if pick is not None and pick <= 16 else ROUND1_LATE
    return PEDIGREE_PRIORS.get(profile.draft_round, UNDRAFTED_PRIOR)


def evidence_decay(years_exp: int | None, career_games: int) -> float:
    """1.0 for the unproven; decays toward 0 as evidence accumulates.

    The pop-vs-space-eater discriminator: a year-1 first-rounder keeps his
    prior, a year-4 first-rounder with three empty seasons has burned it.
    ``career_games`` dominates, so time missed to injury does not burn the prior
    the way empty healthy seasons do (that is durability's entry to this axis).
    """
    evidence = career_games + (years_exp or 0)
    return EVIDENCE_HALF_LIFE / (EVIDENCE_HALF_LIFE + evidence)


def trajectory_nudge(metrics: PlayerSeasonMetrics | None) -> float:
    """Clamped bump for a rising within-season trend (recent form vs season)."""
    if metrics is None or metrics.form_delta_ppr is None:
        return 0.0
    return _clamp(metrics.form_delta_ppr / TRAJECTORY_SCALE, TRAJECTORY_NUDGE_CAP)


def depth_chart_nudge(player: Player) -> float:
    """Clamped bump for a live path to opportunity; 0.0 when unknown."""
    order = player.depth_chart_order
    if order is None:
        return 0.0
    if order <= 1:
        return DEPTH_CHART_NUDGE_CAP
    if order == 2:
        return DEPTH_CHART_NUDGE_CAP * 0.5
    if order >= 4:
        return -DEPTH_CHART_NUDGE_CAP * 0.5
    return 0.0


def market_nudge(trend_add: int) -> float:
    """A small, clamped tilt from consensus adds — never dominates."""
    if not trend_add:
        return 0.0
    return min(MARKET_NUDGE_CAP, trend_add / MARKET_SCALE)


def horizon_seasons(position: str, age: int | None) -> float:
    """Expected seasons of remaining form from the position age curve."""
    peak, max_h, decline = AGE_CURVES.get(position, DEFAULT_AGE_CURVE)
    if age is None:
        return max_h / 2  # positional default; caller sets expires_season=None
    seasons = max_h - max(0, age - peak) * decline
    return max(0.0, min(max_h, seasons))


def blend(
    now: float, prospect: float, horizon: float, profile: str = DEFAULT_PROFILE
) -> float:
    """Weighted sum of the three normalized axes under a weight profile."""
    weights = WEIGHT_PROFILES[profile]
    return (
        weights["now"] * now
        + weights["prospect"] * prospect
        + weights["horizon"] * horizon
    )


def normalize(raws: list[float]) -> list[float]:
    """Scale a list of raw axis values to 0–100; all-equal → a 50.0 constant."""
    if not raws:
        return []
    lo, hi = min(raws), max(raws)
    if hi == lo:
        return [50.0] * len(raws)
    return [(raw - lo) / (hi - lo) * 100 for raw in raws]


def _clamp(value: float, cap: float) -> float:
    return max(-cap, min(cap, value))


def _tier(position_rank: int, position_count: int) -> int:
    """Percentile tier *within a position*: top 5% → 1, top 15% → 2, ….

    Rank-based (``fraction of the position ranked above you``) so it is robust to
    the value distribution's shape and the top player at every position is always
    Tier 1 — including a lone player at a thin position.
    """
    percentile = (position_rank - 1) / position_count  # [0, 1); smaller is better
    for index, cutoff in enumerate(TIER_PERCENTILES, start=1):
        if percentile <= cutoff:
            return index
    return len(TIER_PERCENTILES) + 1


# --- the baseline compute ----------------------------------------------------


def compute_baseline_values(season: int) -> list[PlayerValue]:
    """Build (unsaved) ``PlayerValue`` rows for the whole scoring pool.

    The pool is the union of players with a metrics row for the season, players
    on any tracked roster, and recent draft classes — so a zero-stat rookie or
    taxi stash still gets a value with a real ``prospect_score``.
    """
    players_by_id: dict[int, Player] = {}
    for sm in PlayerSeasonMetrics.objects.filter(
        season=season, season_type="regular"
    ).select_related("player"):
        players_by_id[sm.player_id] = sm.player
    for player in Player.objects.filter(roster_slots__isnull=False).distinct():
        players_by_id.setdefault(player.pk, player)
    for player in Player.objects.filter(
        profile__draft_year__gte=season - PROSPECT_WINDOW
    ):
        players_by_id.setdefault(player.pk, player)

    pool_ids = list(players_by_id)

    metrics_this_season = {
        m.player_id: m
        for m in PlayerSeasonMetrics.objects.filter(
            season=season, season_type="regular", player_id__in=pool_ids
        )
    }
    profiles = {
        p.player_id: p for p in PlayerProfile.objects.filter(player_id__in=pool_ids)
    }
    recent_by_player: dict[int, list[PlayerSeasonMetrics]] = defaultdict(list)
    for m in PlayerSeasonMetrics.objects.filter(
        player_id__in=pool_ids,
        season_type="regular",
        season__gt=season - DURABILITY_WINDOW,
        season__lte=season,
    ):
        recent_by_player[m.player_id].append(m)
    career_games = {
        row["player_id"]: row["g"]
        for row in PlayerSeasonMetrics.objects.filter(
            player_id__in=pool_ids, season_type="regular"
        )
        .values("player_id")
        .annotate(g=Sum("games_played"))
    }
    # Trending is a rolling snapshot, not seasonal — fine for a small market tilt.
    trend = {
        tp.player_id: tp.count
        for tp in TrendingPlayer.objects.filter(
            kind=TrendingPlayer.Kind.ADD, player_id__in=pool_ids
        )
    }

    rows: list[PlayerValue] = []
    for pid, player in players_by_id.items():
        metrics: PlayerSeasonMetrics | None = metrics_this_season.get(pid)
        raw_now = raw_production(metrics) * durability_factor(recent_by_player[pid])
        prior = pedigree_prior(profiles.get(pid))
        decay = evidence_decay(player.years_exp, career_games.get(pid, 0))
        traj = trajectory_nudge(metrics)
        depth = depth_chart_nudge(player)
        market = market_nudge(trend.get(pid, 0))
        raw_prospect = prior * decay + traj + depth + market
        h_seasons = horizon_seasons(player.position, player.age)
        expires = season + round(h_seasons) if player.age is not None else None

        row = PlayerValue(
            player=player,
            season=season,
            model_version=ACTIVE_MODEL_VERSION,
            position=player.position,
            horizon_seasons=h_seasons,
            expires_season=expires,
            value=0.0,  # set after normalization
            updated_at=timezone.now(),
            components={
                "raw_production": raw_production(metrics),
                "durability_factor": durability_factor(recent_by_player[pid]),
                "pedigree_prior": prior,
                "evidence_decay": decay,
                "career_games": career_games.get(pid, 0),
                "trajectory_nudge": traj,
                "depth_chart_nudge": depth,
                "market_nudge": market,
                "raw_now": raw_now,
                "raw_prospect": raw_prospect,
                "horizon_seasons": h_seasons,
            },
        )
        row._raw_now = raw_now  # type: ignore[attr-defined]
        row._raw_prospect = raw_prospect  # type: ignore[attr-defined]
        rows.append(row)

    _normalize_and_rank(rows)
    return rows


def _normalize_and_rank(rows: list[PlayerValue]) -> None:
    now_scores = normalize([r._raw_now for r in rows])  # type: ignore[attr-defined]
    prospect_scores = normalize([r._raw_prospect for r in rows])  # type: ignore[attr-defined]
    horizon_scores = normalize([r.horizon_seasons for r in rows])

    for row, now, prospect, hz in zip(
        rows, now_scores, prospect_scores, horizon_scores, strict=True
    ):
        row.now_score = now
        row.prospect_score = prospect
        row.horizon_score = hz
        row.value = blend(now, prospect, hz)
        row.components.update(
            {"now_score": now, "prospect_score": prospect, "horizon_score": hz}
        )

    for rank, row in enumerate(
        sorted(rows, key=lambda r: r.value, reverse=True), start=1
    ):
        row.overall_rank = rank

    by_position: dict[str, list[PlayerValue]] = defaultdict(list)
    for row in rows:
        by_position[row.position].append(row)
    for position_rows in by_position.values():
        ranked = sorted(position_rows, key=lambda r: r.value, reverse=True)
        count = len(ranked)
        for rank, row in enumerate(ranked, start=1):
            row.position_rank = rank
            row.tier = _tier(rank, count)


VALUE_UPDATE_FIELDS = [
    "position",
    "now_score",
    "prospect_score",
    "horizon_score",
    "horizon_seasons",
    "expires_season",
    "value",
    "tier",
    "position_rank",
    "overall_rank",
    "components",
    "updated_at",
]


def upsert_player_values(rows: list[PlayerValue]) -> int:
    if not rows:
        return 0
    PlayerValue.objects.bulk_create(
        rows,
        batch_size=BATCH_SIZE,
        update_conflicts=True,
        unique_fields=["player", "season", "model_version"],
        update_fields=VALUE_UPDATE_FIELDS,
    )
    return len(rows)


# A model_version → "compute all rows for this season" function. A trained model
# adds an entry here and flips ACTIVE_MODEL_VERSION; nothing else changes.
VALUATION_MODELS: dict[str, Callable[[int], list[PlayerValue]]] = {
    "baseline-v1": compute_baseline_values,
}


def latest_value_season() -> int | None:
    return PlayerSeasonMetrics.objects.aggregate(m=Max("season"))["m"]


def latest_valued_season(model_version: str = ACTIVE_MODEL_VERSION) -> int | None:
    """Newest season with computed ``PlayerValue`` rows for this model version.

    Distinct from ``latest_value_season`` (newest *metrics* season, used to pick
    what to recompute): this tracks the newest *recompute*, so the boards read the
    freshest values without wiring a season into every view.
    """
    return PlayerValue.objects.filter(model_version=model_version).aggregate(
        m=Max("season")
    )["m"]


def with_value_overlay(
    players: QuerySet[Player],
    *,
    season: int | None = None,
    model_version: str = ACTIVE_MODEL_VERSION,
    profile: str = DEFAULT_PROFILE,
) -> QuerySet[Player]:
    """Annotate a ``Player`` queryset with its dynasty value, re-blended at read time.

    The three axes are stored columns, so a weight profile (contend/rebuild/…)
    re-weights the ordering *in SQL* without a recompute — the whole point of PR
    01 storing them separately. Correlated subqueries, so it adds no per-row
    query. Unvalued players annotate to ``None`` and sort last under
    ``nulls_last``.
    """
    if season is None:
        season = latest_valued_season(model_version)
    weights = WEIGHT_PROFILES.get(profile, WEIGHT_PROFILES[DEFAULT_PROFILE])
    pv = PlayerValue.objects.filter(player=OuterRef("pk"), model_version=model_version)
    # No recompute yet → no season to key on; every subquery resolves to None so
    # the boards render "—" instead of erroring.
    pv = pv.filter(season=season) if season is not None else pv.none()

    def axis(field: str) -> Subquery:
        return Subquery(pv.values(field)[:1])

    now, prospect, horizon = (
        axis("now_score"),
        axis("prospect_score"),
        axis("horizon_score"),
    )
    # The profile blend, computed in SQL from the stored axes so a stance switch
    # never needs a recompute. Equals the stored `value` column for the default
    # profile, but computed uniformly so every profile takes one code path.
    dynasty_value = ExpressionWrapper(
        weights["now"] * now
        + weights["prospect"] * prospect
        + weights["horizon"] * horizon,
        output_field=FloatField(),
    )
    return players.annotate(
        dynasty_now=now,
        dynasty_prospect=prospect,
        dynasty_horizon=horizon,
        dynasty_expires=axis("expires_season"),
        dynasty_tier=axis("tier"),
        value_position_rank=axis("position_rank"),
        dynasty_value=dynasty_value,
    )


def recompute_values(
    *,
    season: int | None = None,
    model_version: str = ACTIVE_MODEL_VERSION,
    dry_run: bool = False,
) -> SyncStats:
    """Recompute the valuation pool for a season, idempotently upserting rows.

    Defaults to the latest season present in ``PlayerSeasonMetrics``. Wrapped in
    a ``SyncRun`` (``valuation`` kind). Raises ``ValueError`` for an unknown model
    version or when there are no metrics to value.
    """
    if model_version not in VALUATION_MODELS:
        raise ValueError(f"Unknown model version: {model_version!r}")
    if season is None:
        season = latest_value_season()
        if season is None:
            raise ValueError("No PlayerSeasonMetrics — run recompute_metrics first.")

    compute = VALUATION_MODELS[model_version]
    stats = SyncStats()
    with SyncRun.track(SyncRun.Kind.VALUATION) as run:
        rows = compute(season)
        stats.written = len(rows) if dry_run else upsert_player_values(rows)
        run.records_written = stats.written
    return stats
