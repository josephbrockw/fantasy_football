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

- [ ] `apps/scouting/` app exists and is in `INSTALLED_APPS`; `Target`
      (OneToOne `player`; stance acquire/avoid; tier; priority; notes) and
      `ScoutingNote` (FK `player`; body; timestamped, newest-first) have
      migrations and are registered in the admin
- [ ] The rookie scouting board at `/scouting/rookies/` lists the rookie class
      (`years_exp == 0`) with position and search filters, sortable columns, and
      pagination, reusing `leagues/_player_row.html`
- [ ] From either board I can set a player to Acquire/Avoid, set tier and
      priority, and add a scouting note inline via HTMX; the row re-renders
      without a full page reload, and clearing the stance removes the target
- [ ] The targets board at `/scouting/targets/` lists my acquire/avoid targets,
      shows each player's current roster (team, with a "my team" marker) and is
      grouped/sorted by stance, then tier, then priority
- [ ] Scouting notes for a player display newest-first
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [Scouting app & models](01_scouting-app-and-models.md) | Complete | Reviewed and accepted — 100% covered, quality clean |
| 02 | [Rookie scouting board & inline management](02_rookie-scouting-board.md) | Planned | |
| 03 | [Targets board](03_targets-board.md) | Planned | |

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
