# 04 — Roster views

Feature: `001_sleeper-foundation`

## Objective

Make the synced data usable: a dashboard, my roster, and every rival roster in
the league.

## Scope

**In scope**
- Dashboard replacing the PR 01 placeholder
- My-roster detail view
- League overview (all teams) and rival-team detail views
- HTMX partials for sorting and position filtering
- Shared player-row partial and roster-table partial

**Out of scope**
- Free agents (PR 05)
- Targets, scouting notes, trade tooling (backlog)
- Editing anything — these views are read-only

## Implementation plan

1. **URLs** under `apps/leagues/urls.py`:
   - `/` → dashboard
   - `/league/` → all teams, ordered by record
   - `/team/<int:pk>/` → team detail (also serves my own team)
   - `/team/<int:pk>/table/` → HTMX partial for the roster table
2. **Dashboard** — my record and points, roster counts by position, the league
   standings table, and the freshness of the last `SyncRun` for each kind so a
   stale sync is obvious.
3. **Team detail** — group `RosterSlot` rows by slot (`starter`, `bench`, `taxi`,
   `ir`). Per player show name, position, NFL team, **age**, `years_exp`, rookie
   year, injury status, and depth-chart order. Age and `years_exp` are the columns
   that matter in dynasty, so they are not optional extras here.
4. **Partials** — `_roster_table.html` renders one slot group and is what the
   HTMX endpoint returns; `_player_row.html` is shared with PR 05's free agent
   board, so define it here with a `player` context object and no roster-specific
   assumptions.
5. **Sorting/filtering** via HTMX — `hx-get` on column headers and the position
   filter chips, swapping `#roster-table`. Query params `?sort=age&dir=desc&pos=WR`,
   validated against an allowlist of sortable fields (never interpolate user input
   into `order_by`).
6. **Queries** — `select_related("player")` and `prefetch_related` on the roster
   lookups; the league overview must not N+1 across 12 teams.
7. **Styling** — Tailwind, using the base layout from PR 01. Injury status and
   empty starting slots get colour treatment so problems are visible at a glance.

## Testing

- `test_dashboard_renders_for_synced_league` — 200, shows my team and standings.
- `test_dashboard_handles_no_data` — with an empty DB the dashboard renders a
  "run a sync" empty state instead of raising.
- `test_team_detail_groups_slots` — starters, bench, taxi, and IR appear in their
  own groups with the right players.
- `test_team_detail_404_for_unknown_team`.
- `test_roster_table_partial_returns_fragment` — the HTMX endpoint returns just
  the table, not the full page (assert `base.html` is *not* in the templates used).
- `test_sort_and_filter_params` — `?sort=age&dir=desc` orders correctly;
  `?pos=WR` filters; an invalid `sort` value falls back to the default rather than
  erroring or reaching the ORM.
- `test_league_overview_query_count` — `assertNumQueries` guard against N+1.
- Manual: sync a real league, then browse the dashboard, my roster, and two rival
  rosters; sort by age and filter to WR.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
