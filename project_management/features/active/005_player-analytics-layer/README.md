# 005 — Player analytics layer

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Turn the ingested `PlayerWeekStat` rows (feature 004) into a **materialized,
model-ready feature store** — one derived-metrics row per player and season —
that the player-valuation feature (006) and everything downstream in the ML arc
will consume. This is **deterministic feature engineering, not ML**: every metric
is a reproducible aggregation of realised weekly stats (games played, total and
per-game fantasy points, consistency, recent-form trend, usage proxies), joined
with the player's position/age/experience. It is the *input* to the ML valuation
in 006, not a prediction. Purely additive on the 004 substrate: it reads
`PlayerWeekStat`, makes **no** new Sleeper calls, and touches no existing views.

### Why materialize, not compute on the fly

The metrics are expensive to derive (per-player weekly aggregation, standard
deviation, rolling windows) and are read far more often than the underlying
stats change — a backfill lands once, then 006's valuation, admin, and any report
read the same numbers repeatedly. Computing them on demand would repeat that work
on every query and make the ML feature's inputs non-deterministic across runs. A
materialized `PlayerSeasonMetrics` table recomputed by an explicit command gives
a stable, inspectable, indexable snapshot — the same design rationale as 004's
bounded, idempotent bulk upserts. The recompute reads the DB only (no network),
so it is cheap to re-run whenever new weeks are ingested.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A `PlayerSeasonMetrics` model stores one row per
      `(player, season, season_type)` carrying: `games_played`; season totals
      (`total_ppr` / `total_half_ppr` / `total_std`); per-game averages
      (`ppg_ppr` / `ppg_half_ppr` / `ppg_std`); consistency on weekly PPR
      (`stdev_ppr`, `floor_ppr`, `ceiling_ppr`); recent-form (`recent_ppg_ppr`
      and `form_delta_ppr` = last-N-weeks average minus season average); promoted
      usage proxies (`targets`, `carries`, `snaps`) plus a `usage` JSONField of
      the full summed stat-category dict; and a denormalized `position`. It has a
      `unique_together` on the key, migrations, and admin registration.
- [ ] The metrics are derived **only from realised stats** (`PlayerWeekStat`
      rows with `kind="stat"`), never from projections, and the recompute makes
      **no Sleeper network calls** — it reads the local DB and nothing else.
- [ ] A `recompute_metrics` service and management command (`make
      recompute-metrics`) rebuild the table from `PlayerWeekStat`. With no
      arguments it recomputes every season present; a season range can be
      narrowed via flags. It is **idempotent**: re-running upserts rows in place
      via `bulk_create(update_conflicts=True)` with no duplicates, refreshing
      `updated_at` explicitly (the `TimeStampedModel` `auto_now` is bypassed by
      bulk writes).
- [ ] Each metric is computed correctly and deterministically:
      `games_played` counts only weeks the player actually played; per-game
      values divide totals by `games_played`; `stdev_ppr` is the population
      standard deviation of weekly PPR; `floor_ppr` / `ceiling_ppr` are the min /
      max weekly PPR; recent-form uses the last `RECENT_WINDOW` played weeks; and
      `targets` / `carries` / `snaps` are summed from the correct Sleeper stat
      keys (`rec_tgt` / `rush_att` / `off_snp`), tolerating absent keys.
- [ ] Every recompute run is wrapped in a `SyncRun` (new `metrics` kind) that
      records rows written and skipped and captures failure without leaving a
      half-recorded run.
- [ ] A read-only report command (`python manage.py metrics_report`) prints, per
      season, how many metrics rows exist and the top players by `ppg_ppr`, so a
      recompute can be verified at a glance. No player-facing web view is added.
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered.

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [PlayerSeasonMetrics model & migration](01_playerseasonmetrics-model.md) | Planned | |
| 02 | [Recompute service, command & make target](02_recompute-service-and-command.md) | Planned | |
| 03 | [Read-only metrics report](03_metrics-report.md) | Planned | |

## Definition of Done

The feature is complete only when every box is checked. Then finalize the docs
and move this directory to `features/archived/`.

- [ ] All acceptance criteria verified
- [ ] All new/changed code has test coverage
- [ ] All tests pass (`make test` / `test-runner`)
- [ ] Coverage confirmed (`make coverage` / `coverage-runner`)
- [ ] Code quality confirmed (`make quality` / `quality-runner`)
- [ ] No outstanding build errors
- [ ] Documentation updated
</content>
</invoke>
