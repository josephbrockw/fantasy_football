# 03 — Targets board

Feature: `002_targets-scouting`

## Objective

Ship the Targets board at `/scouting/targets/` — my acquire/avoid list over
rostered players — showing where each player is rostered (with a "my team"
marker), grouped and sorted by stance, tier, then priority, and editable inline
via the management endpoints built in PR 02.

## Scope

**In scope**
- `targets/` (`TargetBoardView`) + `targets/table/` HTMX fragment subclass
- `targets_queryset(...)` annotating each `Target` with its player's
  current-season roster location (team + whether it's my team)
- A stance filter
- Templates `targets_board.html` + `_targets_table.html`, reusing the PR 02
  inline controls and notes
- A nav link to the board

**Out of scope**
- New management endpoints — reuse `set_target` / `add_note` from PR 02
- The rookie board (PR 02)
- Trade evaluation / ML valuation (backlog)

## Implementation plan

This board follows the same view shape as PR 02 but is driven by `Target` rows
rather than the `Player` universe.

1. **URLs** in `apps/scouting/urls.py`:
   - `targets/` → `TargetBoardView` (name `target_board`)
   - `targets/table/` → `TargetTableView` (name `target_board_table`)
2. **`targets_queryset(...)`** in `apps/scouting/views.py`:
   - Base: `Target.objects.select_related("player")`.
   - Annotate the player's **current-season roster location** by joining through
     `RosterSlot → Team → Manager` for the current `LeagueSeason`. The current
     season is the one already used elsewhere (`League.current_season` on the
     tracked league — resolve the same way the free-agent board does, e.g. the
     single/first league's `current_season`; if none is synced, degrade
     gracefully to no roster labels rather than erroring). Use a `Subquery` /
     `OuterRef` to pull the rostering `Team.team_name` (or the manager's display
     name) and a boolean `is_mine` from `Manager.is_me`, scoped to
     `team__league_season=current_season`, so a player rostered only in a past
     season doesn't mislabel.
   - Filter by `stance` (`?stance=acquire|avoid`) when supplied; validate against
     `Target.Stance.values`.
   - **Grouping/sort:** stance, then tier (`nulls_last=True`), then priority.
     Note priority is a `CharField` of choices, so order it deliberately (a
     `Case`/`When` mapping HIGH→0, MEDIUM→1, LOW→2, mirroring
     `PLAYER_POSITION_RANK`) rather than alphabetically, so HIGH sorts above LOW.
3. **Views** — `TargetBoardView(ListView)` and
   `TargetTableView(TargetBoardView)` mirroring PR 02: `paginate_by = 50`,
   `filter_params()` (validate `stance`), `get_queryset()`,
   `get_context_data()` (stance options, current filter, `querystring`), and the
   table subclass overriding only `template_name`.
4. **Templates** under `apps/scouting/templates/scouting/`:
   - `targets_board.html` — extends `base.html`; a stance filter (All / Acquire /
     Avoid chips or a `<select>`) with the same HTMX form pattern
     (`hx-get` → `target_board_table`, `hx-target="#board"`,
     `hx-swap="innerHTML"`); a `<div id="board">` including `_targets_table.html`;
     an empty state ("No targets yet — add one from the rookie board or a
     roster").
   - `_targets_table.html` — the swappable fragment. Each row shows the player
     (reuse `leagues/_player_row.html`), the **stance** badge, **tier**,
     **priority**, and the **rostered-by** team with a "my team" marker when
     `is_mine`, plus the inline controls and notes from PR 02
     (`_target_controls.html`). Rows are grouped/sorted by stance → tier →
     priority per the queryset. Inline edits re-render the row via the existing
     `set_target` / `add_note` endpoints (`hx-target` the row,
     `hx-swap="outerHTML"`); clearing a stance removes the target — its row
     should drop out on the next fragment load.
5. **Nav** — add a "Targets" link alongside the PR 02 "Scouting" link in
   `base.html`'s `{% block nav %}` (or the dashboard).

## Testing

Extend `apps/scouting/tests/test_views.py`. Build on the `LeagueFixture` +
`make_player` helpers, and set up a synced league with my team
(`Manager.is_me=True`) and a rival, each rostering a player that also has a
`Target`. Cover:

- `test_targets_board_lists_my_targets` — all `Target` rows render, with the
  correct stance/tier/priority.
- `test_roster_labels` — a player rostered on my team shows the "my team"
  marker; a player on a rival shows the rival's team; an unrostered target shows
  a blank/"free agent" label rather than erroring.
- `test_roster_label_scoped_to_current_season` — a player rostered only in a past
  `LeagueSeason` is not labelled as currently rostered.
- `test_stance_filter` — `?stance=avoid` lists only avoids; an invalid stance
  falls back to showing all.
- `test_grouping_order` — rows come back ordered stance → tier (nulls last) →
  priority (HIGH before LOW).
- `test_table_endpoint_returns_fragment_not_base` — `targets/table/` renders the
  partial, not `base.html`.
- `test_inline_edit_rerenders` — POSTing to `set_target` from the targets board
  updates the row; clearing the stance deletes the `Target` so it disappears on
  reload.
- `test_empty_state` — with no targets the board renders the empty state.
- `test_query_budget` — `assertNumQueries` guard on the roster-location
  annotation.
- Manual: `make up`, sync a league, add a couple of targets from the rookie
  board and a roster, open `/scouting/targets/`, filter by stance, and confirm
  roster labels and the "my team" marker.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review**. This is the last
PR in the feature — after review, run the full verification gate
(`test-runner`, `coverage-runner`, `quality-runner`), then hand off to
`pm-updater` to archive.
