# 001 — Sleeper Foundation & Roster Tracking

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Stand up the Django project and the Sleeper data layer that every later feature
depends on: a running Dockerised app, a synced player universe, and the league /
roster models that survive Sleeper minting a new `league_id` every season. Ends
with the two views that make the app immediately useful — my roster and the free
agent board.

## Acceptance criteria

- [ ] `docker compose up` starts Django and Postgres; the app serves on `:8000`
- [ ] `make test`, `make coverage`, and `make quality` all run against the
      container and are the documented commands everywhere (no `bb` references
      remain in `CLAUDE.md`, `PROCESS.md`, the templates, or `.claude/`)
- [ ] `make sync-players` stores ~1,045 live players — all 32 NFL teams, all 32
      `DEF` entries, ~225 of the 2026 rookie class — and excludes retired players
      such as Tom Brady who are still flagged `active: true` by Sleeper.
      (`make sync-league` then *adds back* anyone actually rostered in the
      league regardless of the filter, so the stored total exceeds this — the
      invariant is that every teamless player in the table is rostered
      somewhere, not that the table only holds live players.)
- [ ] `make sync-league` resolves a Sleeper username to its leagues and stores
      the league, its seasons, managers, teams, and rostered players
- [ ] A dynasty league whose `league_id` changed between seasons resolves to a
      single `League` with one `LeagueSeason` per year, both via the
      `previous_league_id` chain and via the league-name fallback when that chain
      is broken
- [ ] A player who is on a league roster but excluded by the live-player filter
      (e.g. an unsigned veteran with `team: null`) is still stored, so roster
      sync never fails on a missing foreign key
- [ ] My roster and every rival roster are viewable, showing position, NFL team,
      age, injury status, and rookie year
- [ ] The free agent board lists unrostered players for the current season with
      position and age filters, overlaid with trending add/drop counts

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [Project scaffold & tooling](01_project-scaffold.md) | Complete | Retired the `bb` CLI references |
| 02 | [Sleeper client & player sync](02_sleeper-client-and-players.md) | Complete | 1,045 live players from 12,201 |
| 03 | [League sync with season rollover](03_league-sync.md) | Complete | Verified live: 7 seasons of one dynasty chained back to 2020 |
| 04 | [Roster views](04_roster-views.md) | Complete | Reworked after review: lineup alignment + one card per league |
| 05 | [Free agent board](05_free-agents.md) | In Progress | Implementation done, awaiting review |

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
