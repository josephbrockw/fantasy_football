# 002 — Targets & Scouting

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Add the two decision surfaces a dynasty manager works from between games: a
**Targets board** — rostered players (mine or a rival's) I want to acquire or
avoid in trades — and a **Rookie scouting board** — my hand-tiered draft board
for the upcoming rookie class. Both are backed by two user-owned models
(`Target` = a stance on a player, `ScoutingNote` = free-form observations) and
managed inline with HTMX, so I set stance, tier, priority, and notes without
leaving the page or touching `/admin`. Purely additive on the 001 foundation;
the Sleeper API is untouched — the synced player universe already carries the
`years_exp`, `rookie_year`, `college`, `team`, `position`, and `age` columns
these boards read.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [x] `apps/scouting/` app exists and is in `INSTALLED_APPS`; `Target`
      (FK `player` + FK `league`, `unique(player, league)`; stance acquire/avoid;
      tier; priority; notes) and `ScoutingNote` (FK `player` + FK `league`; body;
      timestamped, newest-first) have migrations and are registered in the admin.
      **Scoped per-league** (matching the free-agent board): the same player can
      be a Tier 1 acquire in one league and untargeted in another
- [x] The rookie scouting board at `/league/<slug>/scouting/rookies/` lists the
      rookie class (`years_exp == 0`) with position and search filters, **grouped
      by position** as a draft board, reusing `leagues/_player_row.html`
- [x] From any board — and from a team's roster screen — I can set a player to
      Acquire/Avoid, set tier and priority, and add a scouting note inline via
      HTMX; the control re-renders in place, and clearing the stance removes the
      target. The control is a reusable component (a lazy-loaded widget on the
      roster screen)
- [x] The targets board at `/league/<slug>/targets/` lists my acquire/avoid
      targets, shows each player's current roster (team, with a "mine" marker or
      "free agent") and is grouped by stance, then tier, then name
- [x] Stance badges are colour-coded (green acquire / red avoid); a shared
      league sub-nav links overview / free agents / rookies / targets
- [x] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [Scouting app & models](01_scouting-app-and-models.md) | Complete | Reviewed and accepted — 100% covered, quality clean |
| 02 | [Rookie scouting board & inline management](02_rookie-scouting-board.md) | Complete | Reworked to per-league after review; grouped-by-position draft board. Reviewed and accepted |
| 03 | [Targets board](03_targets-board.md) | Complete | Per-league targets + roster location + target-from-roster-screen + colour-coded UI/nav. Reviewed and accepted |

## Definition of Done

The feature is complete only when every box is checked. Then finalize the docs
and move this directory to `features/archived/`.

- [x] All acceptance criteria verified
- [x] All new/changed code has test coverage
- [x] All tests pass (`make test` / `test-runner`)
- [x] Coverage confirmed (`make coverage` / `coverage-runner`)
- [x] Code quality confirmed (`make quality` / `quality-runner`)
- [x] No outstanding build errors
- [x] Documentation updated
