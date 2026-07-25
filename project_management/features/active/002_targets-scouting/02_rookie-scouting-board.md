# 02 — Rookie scouting board & inline management

Feature: `002_targets-scouting`

## Objective

Ship the rookie draft board at `/scouting/rookies/` — the upcoming rookie class,
filterable, sortable, paginated — and the inline HTMX management endpoints
(set stance, tier, priority, add a scouting note) that this board and the
Targets board (PR 03) both use. Built by mirroring the 001 free-agent board
wholesale.

## Scope

**In scope**
- `apps/scouting/urls.py` mounted at `/scouting/` in `config/urls.py`
- `rookie_players(...)` queryset fn + `RookieBoardView` / `RookieTableView`
- Inline management endpoints: set a `Target` (create/update/clear) and add a
  `ScoutingNote`, each returning a re-rendered row fragment
- Templates under `apps/scouting/templates/scouting/`
- A nav link to the board

**Out of scope**
- The Targets board and its roster-location annotation (PR 03)
- ML-generated tiers or valuations — tiers here are hand-set (backlog)
- Any write-back to Sleeper — the API is read-only

## Implementation plan

The free-agent board (`apps/leagues/views.py:300-473`,
`apps/leagues/templates/leagues/free_agents.html`,
`_free_agent_table.html`) is the pattern to copy. Follow its conventions
exactly: a `ListView` rendering the full page plus a `.../table/` subclass that
overrides only `template_name` to return the bare HTMX fragment; a module-level
queryset fn with a **whitelisted `SORTS` dict** and `F(...).desc(nulls_last=True)`
(never raw user input in `order_by`); the shared `PLAYER_POSITION_RANK`
annotation for position ordering; `paginate_by = 50`; and `filter_params()` +
`querystring()` helpers.

1. **URLs** — `apps/scouting/urls.py` with `app_name = "scouting"`, mounted in
   `config/urls.py` via `path("scouting/", include("apps.scouting.urls"))`:
   - `rookies/` → `RookieBoardView` (name `rookie_board`)
   - `rookies/table/` → `RookieTableView` (name `rookie_board_table`)
   - `player/<int:pk>/target/` → `set_target` (name `set_target`)
   - `player/<int:pk>/notes/` → `add_note` (name `add_note`)
2. **`rookie_players(...)` queryset fn** in `apps/scouting/views.py`, modelled on
   `free_agents(...)`:
   - Base: `Player.objects.filter(years_exp=0)` (matches `Player.is_rookie`).
     Optionally narrow by `rookie_year` when that filter param is supplied.
   - Filters: `position` (`position=`), `search` (`full_name__icontains`, like
     the free-agent board).
   - Annotate `position_rank=PLAYER_POSITION_RANK` and
     `select_related("target")` so each row can overlay the player's `Target`
     (stance badge, tier, priority) and a `scouting_notes` count without N+1
     (annotate `Count("scouting_notes")` or prefetch — pick one and assert a
     query budget in tests).
   - `SORTS` dict whitelisting `name` (`full_name`), `position`
     (`position_rank`), `age`, `rookie_year`, `college`; default sort `name`.
     Resolve to `F(SORTS.get(sort, SORTS[DEFAULT]))` and order with
     `.desc(nulls_last=True)` / `.asc(nulls_last=True)`, tiebreaking on
     `full_name`.
   - Import `PLAYER_POSITION_RANK` and `POSITION_ORDER` from
     `apps.leagues.views` (or lift the shared constant into a location both apps
     import from — prefer the simple import to avoid churn, and note the coupling
     in the module docstring).
3. **Views** — `RookieBoardView(ListView)` and
   `RookieTableView(RookieBoardView)` mirroring `FreeAgentListView` /
   `FreeAgentTableView`: `filter_params()` (validate `sort` against the
   whitelist, coerce the position/search/rookie_year params), `get_queryset()`,
   `get_context_data()` (pass `columns`, `positions`, current filters,
   `querystring`), and `querystring()` (current filters minus sort/dir/page).
   Positions for the filter chips: the distinct rookie positions, or the
   standard skill set (`QB`, `RB`, `WR`, `TE`) — keep it simple and driven by the
   data.
4. **Inline management endpoints** (built here, reused by PR 03). Small
   POST-only views guarded by CSRF (`{% csrf_token %}` in the inline forms):
   - `set_target(request, pk)` — `get_object_or_404(Player, pk=pk)`, read
     `stance`/`tier`/`priority` from POST. An **empty/blank stance ⇒ delete** any
     existing `Target` (clear); otherwise `Target.objects.update_or_create(
     player=player, defaults={...})`. Return the re-rendered **row fragment** for
     that player so the board updates in place.
   - `add_note(request, pk)` — create a `ScoutingNote(player=player, body=...)`
     from POST `body` (ignore blank); return the row fragment (or a notes
     partial) so the note count / notes list updates in place.
   - Both re-render against the same row partial the table uses, so one player
     object with its `target` + note count is enough context.
5. **Templates** under `apps/scouting/templates/scouting/`:
   - `rookie_board.html` — extends `base.html`; title + filter `form`
     (`hx-get` → `rookie_board_table`, `hx-target="#board"`,
     `hx-swap="innerHTML"`,
     `hx-trigger="change, keyup from:input[name='q'] changed delay:300ms"`);
     hidden `sort`/`dir` inputs; position radio chips and a search box, matching
     `free_agents.html`. A `<div id="board">` includes `_rookie_table.html`.
   - `_rookie_table.html` — the swappable fragment (count line, sortable `<th>`
     buttons carrying `querystring`, pagination carrying `querystring`), the
     `<tbody>` rows `{% include "leagues/_player_row.html" %}` (resolvable
     cross-app via `APP_DIRS`) and then appending the stance/tier/priority
     controls cell. Empty state when no rookies match.
   - `_rookie_row.html` (or reuse the table's row markup) — the fragment the
     management endpoints re-render for a single player: the shared player row
     plus `_target_controls.html`.
   - `_target_controls.html` — the inline controls: a `<select name="stance">`
     with a blank "— none —" option (clears the target), tier and priority
     `<select>`s, and a small note form. `hx-post` to `set_target` /
     `add_note`, `hx-target` the player's row, `hx-swap="outerHTML"`. Show the
     current stance as a badge, the note count, and the newest-first notes when
     present.
6. **Nav** — add a "Scouting" (or "Rookies") link in `base.html`'s
   `{% block nav %}` region (or on the dashboard) pointing at `rookie_board`.

## Testing

Add `apps/scouting/tests/test_views.py`. Reuse a `LeagueFixture`-style
`setUpTestData` and a `make_player(...)` helper (rookie = `years_exp=0`;
veterans = `years_exp>0`). Cover:

- `test_rookie_board_lists_only_rookies` — `years_exp == 0` players appear;
  veterans do not.
- `test_position_and_search_filters` — `?pos=WR` narrows to WRs; `?q=` matches
  partially, case-insensitively.
- `test_sort_whitelist_and_injection_fallback` — a valid `?sort=age&dir=desc`
  orders correctly; a garbage/SQL-injection `sort` value falls back to the
  default rather than reaching `order_by` or erroring.
- `test_table_endpoint_returns_fragment_not_base` — `rookies/table/` renders the
  partial and **not** `base.html` (assert via `response.templates`).
- `test_query_budget` — `assertNumQueries` guard so the `target` overlay and note
  count don't N+1.
- `test_set_target_creates` — POST stance/tier/priority creates a `Target` and
  the returned fragment shows the badge.
- `test_set_target_updates` — POSTing again updates the existing `Target`
  (still one row, `OneToOne`).
- `test_set_target_clear_deletes` — POST with a blank stance deletes the
  `Target`.
- `test_add_note_creates` — POST `body` creates a `ScoutingNote`; a blank body
  is ignored; the count in the fragment increments.
- Manual: `make up`, open `/scouting/rookies/`, filter to WR, set a rookie to
  Acquire with a tier, add a note, and confirm the row updates without a full
  reload.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
