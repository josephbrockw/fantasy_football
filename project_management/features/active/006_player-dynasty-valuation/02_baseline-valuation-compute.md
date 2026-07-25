# 02 — Baseline valuation compute, command & make target

Feature: `006_player-dynasty-valuation`

## Objective

Implement `baseline-v1` as a **three-axis** compute: for every player in the
scoring pool, derive `now_score` (this-season lineup value, from 005's
`PlayerSeasonMetrics`), `prospect_score` (breakout likelihood, from 011's draft
capital with evidence decay plus usage trajectory and market nudge), and the
`horizon` expiration axis (position age curve → `horizon_seasons`,
`expires_season`, `horizon_score`); blend them into `value` via
`WEIGHT_PROFILES["balanced"]`; assign positional tiers and ranks; and upsert
`PlayerValue` rows. Expose it as a `recompute_values` management command and a
`make recompute-values` target, wrapped in a `SyncRun` (new `valuation` kind).
Build the `VALUATION_MODELS` registry seam so a trained model can be added later
without touching the command, the model, or the templates.

## Scope

**In scope**
- `apps/players/valuation.py` — the three sub-score algorithms (each factor a
  named function), `WEIGHT_PROFILES`, the `VALUATION_MODELS` registry,
  `ACTIVE_MODEL_VERSION`, the scoring-pool query, the `recompute_values`
  orchestrator, and its bulk upsert
- `apps/sleeper/models.py` — add `SyncRun.Kind.VALUATION`
- `apps/players/management/commands/recompute_values.py`
- `make recompute-values` target + `.PHONY` entry in the `Makefile`
- Tests: `apps/players/tests/test_valuation.py` and a `recompute_values` case in
  the command tests

**Out of scope**
- The `PlayerValue` model itself (PR 01)
- Every view / template / sort change, including the read-time profile re-blend
  (PR 03)
- Any trained model — only the registry *seam* is built here
- Recomputing 005's metrics or 011's profiles — this command reads
  `PlayerSeasonMetrics` and `PlayerProfile`, it does not produce them

## Dependency note (read first)

This PR consumes **005 `player-analytics-layer`**'s `PlayerSeasonMetrics` *and*
**011 `external-data-enrichment`**'s `PlayerProfile` (draft capital), so **both
must be merged before implementing**. The intended fields:

- From 005: `player` (FK), `season`, `points_per_game`, `recent_form_ppg`,
  `games_played`, and the usage columns (`targets`, `carries`, `snaps`) for the
  trajectory nudge. **Durability and evidence decay read `games_played` across
  multiple seasons** (the last `DURABILITY_WINDOW` for durability; the whole
  career for evidence decay) — the stats backfill starts at `MIN_SEASON = 2018`
  (`apps/players/services.py`), which covers the full career of any player with
  ≤8 years' experience, i.e. the entire population these factors matter for;
  older veterans' career-game counts are floored at the window and that's fine
  (their prior is fully burned regardless).
- From 011: `player.profile.draft_year` / `draft_round` / `draft_pick`
  (nullable — undrafted or unmatched players have no row or null capital).
- From 001's `Player`: `age`, `years_exp`, `position`, `depth_chart_order` /
  `depth_chart_position` (current-state, refreshed by `sync_players` — note in
  a comment that its freshness tracks the last player sync), and roster
  membership via `RosterSlot`.

**Reconcile the exact names against the merged models when you start** — treat
the names below as the contract to confirm, and if something was renamed, update
the reads in one place per axis (`compute_now_score`, `compute_prospect_score`,
`compute_horizon`). No network is involved: the compute reads only DB rows.

## Design decisions

- **Three stored axes, one stored blend.** The sub-scores answer different
  questions (win now / break out later / how long form holds) and the whole
  point of splitting them is re-weighting without recompute. So each axis is
  normalized 0–100 independently across the pool and stored; `value` is the
  `balanced`-profile blend, stored only as the default sort key. Profile
  re-blending is PR 03's read-time job.
- **The pool is bigger than the metrics table.** Iterating
  `PlayerSeasonMetrics` alone would silently skip exactly the players this
  feature exists to value — zero-stat rookies and taxi stashes. The pool is the
  **union** of: players with a metrics row for the season, players currently on
  any tracked roster (`RosterSlot`), and players whose `profile.draft_year` is
  within `PROSPECT_WINDOW` (e.g. 3) years of the season. Missing metrics →
  `now_score = 0`, prospect path still runs.
- **Evidence decay is the discriminator.** A pedigree prior that never decays
  would keep rating a busted year-4 first-rounder as a prospect forever; no
  prior at all collapses every zero-stat youngster to the same value. The decay
  (driven by `years_exp` and career games) is what separates "good odds to pop"
  from "eating a roster spot" — it gets its own named function and its own
  tests.
- **Deterministic and inspectable over accurate.** Every factor is a small pure
  function returning a float, and the compute records each into the
  `components` dict it stores. A weird value is then explainable from the admin
  without re-running anything.
- **Registry seam.** A module-level
  `VALUATION_MODELS: dict[str, Callable[[int], list[PlayerValue]]]` maps a
  `model_version` to a "compute all rows for this season" function.
  `ACTIVE_MODEL_VERSION = "baseline-v1"` names the one the app reads (PR 03).
  A future `trained-v1` registers another entry and is activated by changing that
  constant — the command dispatches on `--model-version`, defaulting to active.
- **Idempotent upsert on the natural key**, exactly like `upsert_week_stats` in
  `services.py`: `bulk_create(update_conflicts=True, unique_fields=[...])`, and
  because that path bypasses `TimeStampedModel.auto_now`, set `updated_at`
  explicitly on each instance (the caveat `sync_players` / `sync_stats` already
  work around — see `CLAUDE.md`).

## Implementation plan

1. **`SyncRun.Kind.VALUATION`** in `apps/sleeper/models.py`:

   ```python
   VALUATION = "valuation", "Valuation"
   ```
   This makes recompute freshness show up on the dashboard's sync-runs card for
   free (`DashboardView` iterates `SyncRun.Kind.choices`). It is a compute, not a
   network sync, but `SyncRun` is the project's audit-log + freshness pattern.

2. **Constants & curves** in a new `apps/players/valuation.py`:
   - `ACTIVE_MODEL_VERSION = "baseline-v1"`.
   - `WEIGHT_PROFILES` — the blend weights, each summing to 1.0:

     ```python
     WEIGHT_PROFILES: dict[str, dict[str, float]] = {
         "balanced": {"now": 0.45, "prospect": 0.35, "horizon": 0.20},
         "contend":  {"now": 0.70, "prospect": 0.15, "horizon": 0.15},
         "rebuild":  {"now": 0.20, "prospect": 0.55, "horizon": 0.25},
     }
     DEFAULT_PROFILE = "balanced"
     ```
     The stored `value` always uses `DEFAULT_PROFILE`; PR 03 re-blends the
     others at read time. Weights are starting points to tune, not gospel —
     comment that.
   - `AGE_CURVES` — per position, `(peak_age, decline_rate, max_seasons)`
     params backing both `horizon_seasons` (expiration) and nothing else — the
     now axis deliberately ignores age. RB declines early (~27 wall), WR/TE
     flatter and later, QB longest, K/DEF flat. Missing age → the positional
     default seasons, `expires_season = None`.
   - `PEDIGREE_PRIORS` — draft-capital prior by round (round 1 highest, with an
     early/late round-1 split via overall pick if available; declining by round;
     `UNDRAFTED_PRIOR` small constant; missing profile → same constant).
   - `EVIDENCE_HALF_LIFE` — the `years_exp` / games-played scale on which the
     pedigree prior decays.
   - `PROSPECT_WINDOW` (e.g. `3`) — draft classes included in the pool.
   - `DURABILITY_WINDOW` (e.g. `3` seasons) and `DURABILITY_FLOOR` (e.g. `0.75`)
     — the games-played-rate lookback and the clamp lower bound, so durability
     discounts availability risk without ever zeroing a producer.
   - `PRODUCTION_RECENT_WEIGHT` (e.g. `0.3`), `MARKET_NUDGE_CAP` (e.g. `0.1`),
     `TRAJECTORY_NUDGE_CAP`, `DEPTH_CHART_NUDGE_CAP` — all named constants, no
     magic numbers inline.
   - `TIER_BREAKS` — the blended-value bands that map to tiers 1..N per
     position (or compute tiers by positional quantiles; pick one and comment
     the choice).

3. **Axis functions** in `apps/players/valuation.py`, each tiny and unit-tested:

   ```python
   def raw_production(metrics: PlayerSeasonMetrics | None) -> float:
       """Season ppg nudged toward recent form; 0.0 when never played."""
       if metrics is None:
           return 0.0
       ppg = metrics.points_per_game or 0.0
       recent = metrics.recent_form_ppg or ppg
       return (1 - PRODUCTION_RECENT_WEIGHT) * ppg + PRODUCTION_RECENT_WEIGHT * recent

   def durability_factor(recent_metrics: list[PlayerSeasonMetrics]) -> float:
       """Games-played rate over the last DURABILITY_WINDOW seasons.

       Clamped to [DURABILITY_FLOOR, 1.0]; 1.0 when there is no history to
       judge (a rookie is not injury-prone by default). Multiplies raw now
       production — availability is part of this-year value.
       """

   def pedigree_prior(profile: PlayerProfile | None) -> float:
       """Draft-capital prior: round 1 high, by-round decline, undrafted floor."""

   def evidence_decay(years_exp: int | None, career_games: int) -> float:
       """1.0 for the unproven; decays toward 0 as evidence accumulates.

       The pop-vs-space-eater discriminator: a year-1 first-rounder keeps his
       prior, a year-4 first-rounder with three empty seasons has burned it.
       ``career_games`` counts games actually played, so time missed to injury
       does not burn the prior the way empty healthy seasons do — this is
       durability's entry point into the prospect axis.
       """

   def trajectory_nudge(metrics: PlayerSeasonMetrics | None) -> float:
       """Clamped bump for rising usage (targets/snaps); 0 when unknown."""

   def depth_chart_nudge(player: Player) -> float:
       """Clamped bump for a live path to opportunity.

       ``depth_chart_order`` 1–2 nudges up, deep reserve slightly down, missing
       depth chart → 0.0 (neutral). Opportunity leads production — a prospect
       who just won a starting job is closer to popping than his box scores
       show.
       """

   def market_nudge(trend_add: int) -> float:
       """A small, clamped tilt from consensus adds — never dominates."""

   def horizon_seasons(position: str, age: int | None) -> float:
       """Expected seasons of remaining form from the position age curve."""

   def blend(now: float, prospect: float, horizon: float,
             profile: str = DEFAULT_PROFILE) -> float:
       """Weighted sum of the three normalized axes."""
   ```

4. **Per-player raws** — `compute_player_axes(player, metrics, recent_metrics,
   profile, trend_add, season)` returns `(raw_now, raw_prospect,
   horizon_seasons, expires_season, components)` where
   `raw_now = raw_production(metrics) * durability_factor(recent_metrics)` and
   `raw_prospect = pedigree_prior(...) * evidence_decay(...) +
   trajectory_nudge(...) + depth_chart_nudge(...) + market_nudge(...)`. The
   components dict carries every factor (`raw_production`, `durability_factor`,
   `pedigree_prior`, `evidence_decay`, `trajectory_nudge`, `depth_chart_nudge`,
   `market_nudge`, `horizon_seasons`), so PR 01's JSON contract is honoured.

5. **Normalization + blending + ranking** —
   `normalize(raws: list[float]) -> list[float]` scaling to 0–100 (guard an
   all-equal / empty pool → a sensible constant, not a divide-by-zero), applied
   **independently per axis** to produce `now_score`, `prospect_score`, and
   `horizon_score`; then `value = blend(...)` under `DEFAULT_PROFILE`.
   `assign_positional_tiers_and_ranks(rows)` sorts within `position` by `value`
   desc to set `position_rank` and `tier`, and across the pool for
   `overall_rank`.

6. **`compute_baseline_values(season)`** — the registry entry:
   - **Build the pool** (the union, deduped by player id):
     `PlayerSeasonMetrics.objects.filter(season=season).select_related("player__profile")`,
     plus `Player.objects.filter(roster_slots__isnull=False)` (distinct), plus
     `Player.objects.filter(profile__draft_year__gte=season - PROSPECT_WINDOW)`.
     Fetch metrics into a `{player_id: metrics}` dict so pool members without a
     row get `None`. In the same pass, fetch the **multi-season** metrics the
     other factors need: a `{player_id: [metrics, ...]}` map over the last
     `DURABILITY_WINDOW` seasons (for `durability_factor`) and a
     `{player_id: career_games}` sum of `games_played` across all stored
     seasons (for `evidence_decay`) — three dict lookups per player, no
     per-player queries.
   - Resolve current market signal once:
     `{tp.player_id: tp.count for tp in TrendingPlayer.objects.filter(kind=ADD)}`
     (trending is a rolling snapshot, not seasonal — acceptable for a market
     nudge; note this in a comment).
   - For each pool player: compute the axes + components, build an **unsaved**
     `PlayerValue` (snapshot `player.position`, `season`,
     `model_version="baseline-v1"`, `updated_at=timezone.now()`), stashing the
     raw axis values on the instance for the normalization pass.
   - Normalize the three axes across the pool → set the sub-score columns;
     blend → `value`; assign tiers/ranks; fold raws and normalized numbers into
     each `components`. Return the list (unsaved).

7. **`recompute_values(...)` orchestrator** in `apps/players/valuation.py`:

   ```python
   def recompute_values(
       *, season: int | None = None,
       model_version: str = ACTIVE_MODEL_VERSION,
       dry_run: bool = False,
   ) -> SyncStats:
   ```
   - Default `season` to the latest season present in `PlayerSeasonMetrics`
     (`PlayerSeasonMetrics.objects.aggregate(Max("season"))`), erroring cleanly
     if there are none.
   - Dispatch: `compute = VALUATION_MODELS[model_version]` (KeyError → a clear
     error the command turns into `CommandError`).
   - Wrap in `with SyncRun.track(SyncRun.Kind.VALUATION) as run:`; build rows,
     and unless `dry_run`, `upsert_player_values(rows)` (a bulk upsert mirroring
     `upsert_week_stats`, `unique_fields=["player", "season", "model_version"]`,
     `update_fields=["position", "now_score", "prospect_score", "horizon_score",
     "horizon_seasons", "expires_season", "value", "tier", "position_rank",
     "overall_rank", "components", "updated_at"]`).
     Set `run.records_written`. Reuse the `SyncStats` dataclass from
     `services.py` (import it) for the return type.

8. **`VALUATION_MODELS` registry** at module scope, after the functions:

   ```python
   VALUATION_MODELS: dict[str, Callable[[int], list[PlayerValue]]] = {
       "baseline-v1": compute_baseline_values,
   }
   ```
   Comment: a trained model adds an entry here and flips `ACTIVE_MODEL_VERSION`;
   nothing else changes.

9. **Command** `apps/players/management/commands/recompute_values.py`, modelled on
   `sync_stats.py`:
   - `--season` (int, default None → latest metrics season),
   - `--model-version` (default `ACTIVE_MODEL_VERSION`),
   - `--dry-run`.
   - Call `recompute_values(...)`; on an unknown `--model-version` or "no metrics"
     raise `CommandError`; print
     `self.style.SUCCESS(f"Wrote {stats.written} value(s) for season {season} ({model_version}).")`.

10. **Makefile** — add next to `sync-stats`, and to `.PHONY`:

    ```make
    recompute-values:  ## Recompute dynasty player values & tiers
    	$(EXEC) python manage.py recompute_values $(ARGS)
    ```

## Testing

`apps/players/tests/test_valuation.py` (Django `TestCase`, **no network**). Build
`Player`, `PlayerProfile`, `PlayerSeasonMetrics`, `RosterSlot`, and
`TrendingPlayer` rows directly with a small factory. Cover:

- **Now axis** — `test_raw_production_blends_recent_form` (recent form shifts
  the blend the expected direction), `test_raw_production_zero_without_metrics`
  (no metrics row → 0.0, not an error),
  `test_durability_factor` (a player who played 30 of the last 51 games scores
  lower than one who played 50; the clamp holds at `DURABILITY_FLOOR`; no
  history → 1.0), `test_durability_discounts_now_score` (two players with equal
  per-game production but different games-played rates end with different
  `now_score`s, injury-prone lower).
- **Prospect axis** —
  `test_pedigree_prior_by_round` (round 1 > round 3 > undrafted; missing
  profile → the undrafted floor),
  `test_evidence_decay` (year-0/1 with few games ≈ 1.0; year-4 with three full
  seasons ≪ 1.0; bounded in `[0, 1]`),
  `test_prospect_discriminates_pedigree` — **the feature's core promise**: two
  zero-stat same-age players, one a round-1 rookie and one undrafted, end with
  clearly different `prospect_score`s; and a year-4 first-rounder with empty
  seasons scores below the round-1 rookie,
  `test_trajectory_nudge_clamped`, `test_market_nudge_is_clamped`,
  `test_depth_chart_nudge` (order 1 > order 4; missing depth chart → 0.0
  exactly, never a penalty; clamped both directions), and
  `test_depth_chart_moves_prospect_score` — two otherwise-identical zero-stat
  rookies, one `depth_chart_order=1` and one `4`, end with the starter's
  `prospect_score` higher.
- **Horizon axis** — `test_horizon_seasons_curve` (a 23-yr-old RB > a
  28-yr-old RB; a 28-yr-old QB > a 28-yr-old RB; unknown age → positional
  default with `expires_season=None`; results non-negative),
  `test_expires_season_derivation` (`season + round(horizon_seasons)`).
- **Blend** — `test_blend_profiles` (weights sum to 1.0 for every profile;
  `contend` ranks a high-`now` veteran above a high-`prospect` rookie and
  `rebuild` flips them — the re-weighting promise, tested at the function level
  here and at the view level in PR 03), `test_stored_value_uses_default_profile`.
- **Pool** — `test_pool_includes_zero_stat_prospects` — a rostered rookie with
  no `PlayerSeasonMetrics` row still yields a `PlayerValue` row
  (`now_score = 0`, `prospect_score > 0`); an unrostered veteran outside the
  prospect window with no metrics gets **no** row.
- `test_normalize_scales_to_0_100` — top raw → ~100, and an all-equal pool
  doesn't divide by zero; each axis normalizes independently.
- `test_compute_baseline_orders_by_value` — given a few players with known
  inputs, the produced rows rank as expected; `position_rank` restarts per
  position; `components` contains every factor key (the inspection contract).
- `test_recompute_upserts_and_is_idempotent` — running twice yields no duplicate
  rows on the natural key, a changed metric updates the sub-scores and `value`
  in place, and `updated_at` advances (proves the explicit-`auto_now`
  workaround).
- `test_recompute_defaults_to_latest_season` — with metrics for 2024 and 2025, a
  no-arg run values 2025.
- `test_recompute_wraps_in_syncrun` — a successful run leaves a
  `SyncRun(kind="valuation", status="success")` with `records_written` set;
  simulate a failure (e.g. patch the compute to raise) and assert
  `status == "failed"` with no partial write.
- `test_dry_run_writes_nothing`.
- `test_unknown_model_version` — `recompute_values(model_version="nope")` /
  the command raise a clear error.
- Command tests in `test_commands.py`: `test_recompute_values_command`
  (invoke via `call_command`, assert the success line + rows written),
  `test_recompute_values_command_no_metrics_errors` (empty `PlayerSeasonMetrics`
  → `CommandError`).
- Manual: `make recompute-values ARGS="--season 2025"` then a shell check that
  a known rookie has a `PlayerValue` with `now_score=0` and a non-trivial
  `prospect_score`, and a spot value's `components` reads sensibly.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
