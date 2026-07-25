# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is a Django app for managing my Dynasty fantasy football team. I want to be able review my roster, track other teams in my league, free agents, draft scouting, and more. Eventually, I want to add ML to make more informed decisions.

Data comes from the [Sleeper API](https://docs.sleeper.com/) — public, read-only, no auth.

## Running the project

Everything runs in Docker (Django + Postgres). The `Makefile` is the command
surface; it wraps `docker compose exec web …`, so use it rather than calling
`docker compose` or `manage.py` directly. `make help` lists every target.

```bash
make build && make up     # start the stack (app on :8000, Postgres on :5433)
make migrate
make test                 # ARGS="apps.players" to narrow
make coverage
make quality              # ruff check --fix, ruff format, mypy
make sync-players         # pull the Sleeper player universe
make sync-league          # pull leagues/rosters (needs SLEEPER_USERNAME in .env)
make sync-trending        # pull trending add/drop counts (free-agent board)
```

Postgres is published on host port **5433**, not 5432, to avoid colliding with a
local Postgres install.

## Layout

- `config/` — Django project (env-driven `settings.py` via django-environ)
- `apps/core/` — the shared `TimeStampedModel` abstract base
- `apps/sleeper/` — API client, sync services, `SyncRun` audit log
- `apps/players/` — the `Player` universe
- `apps/leagues/` — `League`, `LeagueSeason`, `Manager`, `Team`, `RosterSlot`, plus
  all the server-rendered views and templates: the dashboard (the site root),
  league overview, roster / team detail, and the free-agent board
- `templates/base.html`, `static/` — the base layout, HTMX, and a built Tailwind stylesheet

Frontend is server-rendered Django templates with HTMX for partial updates and
Tailwind for styling. Tailwind is compiled by the standalone binary via
`make css`; the built `static/css/app.css` is committed so nothing needs Node at
runtime.

## Sleeper API gotchas

Two traps worth knowing before touching sync code:

- **`active` is not a liveness signal.** Sleeper reports Tom Brady, Drew Brees
  and Antonio Brown as `active: true`. Filter on `team` being non-null instead —
  that reduces 12,200 players to ~1,043 real ones. The league sync must still
  upsert any `player_id` it sees on a roster, bypassing the filter, or roster
  writes fail on a missing FK.
- **`search_rank` is not an ADP.** It collides heavily (1,436 players share the
  sentinel `9999999`) and is only useful as a coarse tiebreak for search
  ordering. Player valuation is the ML feature's job.
- **A dynasty league gets a new `league_id` every season.** `League` is the
  permanent record; `LeagueSeason` is the per-year Sleeper league. Seasons are
  chained via `previous_league_id`, falling back to a normalized name match.
- **`starters` is positional, not a set.** `starters[i]` fills the i-th entry of
  the league's starting slots (`roster_positions` minus `BN`/`IR`/`TAXI`), and
  `"0"` marks an empty slot without shifting later indices. `RosterSlot`
  persists this as `lineup_order` / `lineup_position`. Drop it and a legal
  superflex roster reads as two quarterbacks started at once.
- **Sleeper 404s on an unknown league** but returns `200 null` for an unknown
  user and `200 []` for an unknown league's `/users`. `get_league` and
  `get_league_rosters` swallow the 404 so a purged `previous_league_id` ends the
  chain walk instead of raising.

## Project management

Feature planning and tracking live in `project_management/`, governed by **`project_management/docs/PROCESS.md`** — read it before planning or completing a feature. In brief:

- Features are directories `features/active/{NNN}_{name}/` (moved to `features/archived/` when done — location is the status, there is no status field). Each has a `README.md` (goals, acceptance criteria, PR table, Definition of Done) and one detailed plan per PR, `{NN}_{name}.md`. `features/BACKLOG.md` holds future ideas; `project_management/templates/` holds the README/PR-plan templates.
- **Review checkpoint:** after finishing a PR's implementation, STOP for the user to review before starting the next PR. PR statuses are `Planned → In Progress → Complete`.
- **Completion gate:** a feature is done only when its acceptance criteria are verified and `make test`, `make coverage`, and `make quality` all pass (fix any build errors along the way). Then docs are updated and the directory is archived.

Subagents for this flow (in `.claude/agents/`): `pm-planner` (scaffold a feature + PR plans, promote from BACKLOG), `pm-updater` (PR status updates; archive a finished feature), and the verification trio `test-runner` / `coverage-runner` / `quality-runner`. Subagents can't call each other, so the main thread runs the verification agents, then hands archival to `pm-updater`.

### Tests (Django)

Use the Django testing utilities. We want full coverage.

