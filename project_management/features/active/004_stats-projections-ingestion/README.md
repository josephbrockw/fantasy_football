# 004 — Stats & Projections Ingestion

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Ingest weekly NFL player **stats** and **projections** from Sleeper's
undocumented-but-working `/stats` and `/projections` endpoints, and make the
sync capable of a **full historical backfill** — every available season and week
up front, not just the current one. This is the training substrate the future
ML dynasty-valuation feature will learn from, so the priority is completeness,
idempotent re-runs, and efficient bulk writes over any UI. Purely additive on
the 001 foundation: it reuses the existing `SleeperClient`, the `SyncRun` audit
log, and the `Player` universe, and touches no existing views.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A single `PlayerWeekStat` model stores one row per
      `(player, season, week, season_type, kind)` where `kind` is `stat` or
      `projection`, carrying the full Sleeper stat-category dict in a JSONField
      plus promoted, nullable scoring columns (`pts_ppr`, `pts_half_ppr`,
      `pts_std`). It has a `unique_together` on that key and migrations, and is
      registered in the admin. One table with a `kind` discriminator is used for
      both endpoints (justified in PR 01).
- [ ] `SleeperClient` exposes `get_player_stats(season, week, ...)` and
      `get_player_projections(season, week, ...)` hitting
      `/v1/stats/nfl/{season_type}/{season}/{week}` and
      `/v1/projections/nfl/{season_type}/{season}/{week}`; both tolerate an empty
      (`{}` / `null`) body for a season/week Sleeper has no data for, and no test
      hits the network.
- [ ] `make sync-stats` runs a backfill: with no arguments it pulls **every**
      season from the configured earliest through the current season (resolved
      from `get_nfl_state`), weeks 1–18, for **both** stats and projections; a
      season/week/kind range can be narrowed via flags, and a `--kind` flag can
      restrict to just stats or just projections.
- [ ] The sync is **idempotent**: re-running over the same range updates rows in
      place via bulk upsert (`bulk_create(update_conflicts=True)`) with no
      duplicates, and `updated_at` is refreshed explicitly (the
      `TimeStampedModel` `auto_now` is bypassed by bulk writes).
- [ ] Stat rows for player ids **not** in the `Player` table are skipped and
      counted (mirroring `sync_trending`), so a full week of ~550 KB keyed by the
      whole Sleeper universe never fails on a missing foreign key and storage
      stays bounded to tracked players.
- [ ] Every run is wrapped in a `SyncRun` (new `stats` kind) that records
      rows written and skipped, and captures failure without leaving a
      half-recorded run.
- [ ] A coverage report (`python manage.py stats_coverage`) prints, per season,
      which weeks have stat and projection rows and the row counts, so a backfill
      can be verified at a glance.
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered.

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [PlayerWeekStat model & migration](01_playerweekstat-model.md) | Planned | |
| 02 | [Stats client, backfill sync & command](02_stats-sync-and-command.md) | Planned | |
| 03 | [Backfill coverage report](03_coverage-report.md) | Planned | |

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
