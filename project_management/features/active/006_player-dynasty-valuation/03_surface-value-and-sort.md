# 03 — Surface value across boards & value sorting

Feature: `006_player-dynasty-valuation`

## Objective

Make the dynasty value visible and useful in the app: show value + tier on the
shared `leagues/_player_row.html` (so it appears at once on the roster,
free-agent, rookie, and targets boards) with the three sub-scores
(now / prospect / horizon + "holds form through") available on hover, add
value-based sorting to the free-agent board with value as the new default order,
add a **weight-profile selector** (`balanced` / `contend` / `rebuild`) that
re-blends the ordering **at read time** from the stored sub-score columns, and
replace the coarse `search_rank` ordering/tiebreak on the free-agent and rookie
boards with the real `PlayerValue`. This is the payoff PR — the point of the
whole feature.

## Scope

**In scope**
- A shared `with_value_overlay(...)` queryset annotation for `Player` querysets
- `leagues/_player_row.html` — a value + tier cell
- `apps/leagues/views.py` — overlay + value sort/tiebreak on the free-agent board;
  overlay on the reserves queryset
- `apps/leagues/templates/leagues/_reserve_tables.html` and
  `_free_agent_table.html` — pass/label the value cell/column
- `apps/scouting/views.py` — overlay + value tiebreak on the rookie & targets
  boards
- Tests updating the leagues and scouting view suites

**Out of scope**
- The model (PR 01) and the compute (PR 02)
- Any new board or page; a player value-history view
- Re-styling the boards beyond the one new cell/column

## The overlay wrinkle (decide up front)

`_player_row.html` renders in two shapes of loop:

- **`Player` querysets** — free agents, rookies, targets iterate players
  directly. Annotating the queryset lands `player.dynasty_value` etc. on each row.
- **`RosterSlot` queryset** — the reserves table iterates `slot` and includes the
  row `with player=slot.player`. An annotation on the `RosterSlot` queryset lands
  on `slot`, **not** on `slot.player`, so `player.dynasty_value` would be empty
  there.

Resolve it by having the row template read **top-level context vars with a player
fallback**, so it stays roster-agnostic:

```django
{% with val=dynasty_value|default:player.dynasty_value tier=dynasty_tier|default:player.dynasty_tier %}
  ... render val / tier ...
{% endwith %}
```

- Player-queryset callers annotate `dynasty_value` / `dynasty_tier` **onto the
  player** → the `player.dynasty_value` branch fires.
- The reserves table annotates the `RosterSlot` queryset and passes the values
  explicitly: `{% include "leagues/_player_row.html" with player=slot.player
  dynasty_value=slot.dyn_value dynasty_tier=slot.dyn_tier %}`.

The row never references `slot`, so it keeps the "no roster-specific assumptions"
rule the file's comment states.

## Implementation plan

1. **Shared overlay helper.** Add `with_value_overlay(players, *, season=None,
   model_version=ACTIVE_MODEL_VERSION, profile=DEFAULT_PROFILE)` to
   `apps/players/valuation.py` (or a small `apps/players/queries.py` if you'd
   rather keep `valuation.py` compute-only — pick one and note it). It annotates
   a `Player` queryset via correlated subqueries on `PlayerValue`, mirroring
   scouting's `_with_target_overlay`, and **re-blends the sub-scores under the
   requested profile in the query** — this is why PR 01 made them columns:

   ```python
   def with_value_overlay(players, *, season=None,
                          model_version=ACTIVE_MODEL_VERSION,
                          profile=DEFAULT_PROFILE):
       season = season or latest_value_season(model_version)
       w = WEIGHT_PROFILES[profile]
       pv = PlayerValue.objects.filter(
           player=OuterRef("pk"), model_version=model_version, season=season
       )
       return players.annotate(
           dynasty_now=Subquery(pv.values("now_score")[:1]),
           dynasty_prospect=Subquery(pv.values("prospect_score")[:1]),
           dynasty_horizon=Subquery(pv.values("horizon_score")[:1]),
           dynasty_expires=Subquery(pv.values("expires_season")[:1]),
           dynasty_tier=Subquery(pv.values("tier")[:1]),
           value_position_rank=Subquery(pv.values("position_rank")[:1]),
           # The profile blend, computed in SQL from the stored axes so a
           # stance switch never needs a recompute.
           dynasty_value=(
               w["now"] * F("dynasty_now")
               + w["prospect"] * F("dynasty_prospect")
               + w["horizon"] * F("dynasty_horizon")
           ),
       )
   ```
   `latest_value_season(model_version)` = `Max("season")` over `PlayerValue` for
   that version (so the app tracks the newest recompute without wiring a season
   into every view). No extra query per row — subqueries, like the target
   overlay. (If annotating over the subquery aliases proves awkward in the ORM,
   wrap the arithmetic in `ExpressionWrapper(..., output_field=FloatField())` or
   fall back to blending over three inline `Subquery(...)` expressions — decide
   in the PR; the contract is "blend happens in the queryset".) For
   `profile == DEFAULT_PROFILE` the blend numerically equals the stored `value`
   column; still compute it uniformly so every profile takes the same code path.

2. **Row template** — `apps/leagues/templates/leagues/_player_row.html`: add a
   value/tier cell using the `{% with %}` fallback from above. Render the value as
   a rounded integer with a tier badge (reuse the badge styling already in the
   file — the emerald "R" / red injury spans are the pattern); show `—` when
   unvalued (unrostered-but-unscored players, or before the first recompute). Put
   the sub-scores in the cell's `title` attribute (e.g.
   `Now 62 · Prospect 78 · Horizon 71 · holds form through 2029`) so the
   three-axis breakdown is a hover away without widening any table. Keep it a
   single `<td>` so every table's column count math still works.

3. **Free-agent board** (`apps/leagues/views.py`):
   - Import `with_value_overlay` (and `ACTIVE_MODEL_VERSION` as needed).
   - In `free_agents(...)`, apply `with_value_overlay(players, ...)` alongside the
     existing `position_rank` / `trend_*` annotations.
   - Add `"value": "dynasty_value"` to `FREE_AGENT_SORTS` and make
     `FREE_AGENT_DEFAULT_SORT = "value"` (dynasty value is the natural default
     order for a free-agent board).
   - **Replace the `search_rank` tiebreak** in the final `order_by` with
     `F("dynasty_value").desc(nulls_last=True)` (then `full_name`). Delete the
     `search_rank` comment/clause — this is the line the whole feature exists to
     kill.
   - Add `("value", "Value")` to `FREE_AGENT_COLUMNS` (near the front, e.g. right
     after Player) so the sortable header renders.
   - `FreeAgentListView.filter_params` already validates `sort` against
     `FREE_AGENT_SORTS`, so the new key is safe automatically.
   - **Profile selector**: accept a `?profile=` query param, validated against
     `WEIGHT_PROFILES` (unknown → `DEFAULT_PROFILE`), passed through to
     `with_value_overlay(..., profile=profile)` so the blend — and therefore the
     ordering — re-weights at read time. Render it as a small pill/select next
     to the existing position filter (HTMX swap like the sort headers), and
     carry it in the sort links so switching sort keeps the stance. This is the
     contend-vs-rebuild toggle the three-axis design exists for.
   - In `_free_agent_table.html`, add the `value` key to the right-align check in
     the `<th>` class conditional (it's a numeric column). The value cell itself
     comes from the shared row include; the standalone trending `<td>` stays.

4. **Reserves / roster** (`apps/leagues/views.py` + `_reserve_tables.html`):
   - In `reserve_slots(...)`, annotate the `RosterSlot` queryset with
     `dyn_value` / `dyn_tier` via `PlayerValue` subqueries keyed on
     `OuterRef("player_id")` (same active version + latest season). Add a
     `Value`/`Tier` header cell to `_reserve_tables.html`'s `<thead>` (kept
     non-sortable — reserves sort by the existing whitelist) and pass the values
     into the row include with `{% include ... with player=slot.player
     dynasty_value=slot.dyn_value dynasty_tier=slot.dyn_tier %}`. This is the
     RosterSlot half of the overlay wrinkle above.
   - The starting-lineup rows in `team_detail.html` render players too — decide in
     the PR whether to extend the overlay there or leave the lineup value-free for
     now (the acceptance criterion is satisfied by reserves + the three
     Player-queryset boards). If included, annotate `starting_lineup`'s query the
     same way; keep it in scope-note either way.

5. **Rookie & targets boards** (`apps/scouting/views.py`):
   - Import `with_value_overlay`.
   - In `rookie_players(...)`, apply the overlay and **replace**
     `F("search_rank").asc(nulls_last=True)` in the `order_by` with
     `F("dynasty_value").desc(nulls_last=True)` — the value-based ordering for the
     position-grouped rookie board (the board is grouped, not column-sorted, so
     value ordering *is* the sort here). Update the docstring comment that
     currently explains the `search_rank` proxy.
   - In `targeted_players(...)`, apply the overlay so the value cell populates on
     the targets board (ordering there stays stance → tier → name).
   - The scouting boards render the shared row through `_rookie_table.html` /
     `_targets_table.html`; because those iterate `player`, the annotated
     `player.dynasty_value` flows into the row with no template change beyond
     confirming the column-count/`colspan` still matches after the row gains a
     cell. Adjust any hard-coded `colspan` in the scouting empty-state rows.

6. **Query budgets** — the overlay is subqueries, so it must not add a query per
   row. Keep/extend the `assertNumQueries` guards the boards already have.

## Testing

Extend the existing suites; all Django `TestCase`, no network. Seed a couple of
`PlayerValue` rows (active `model_version`, current season) in `setUpTestData`.

- **leagues** (`apps/leagues/tests/test_free_agent_views.py` or equivalent):
  - `test_default_sort_is_value` — no `?sort` orders free agents by dynasty value
    desc; a higher-value player precedes a lower one.
  - `test_value_sort_and_direction` — `?sort=value&dir=asc` flips the order;
    unvalued players sort last (`nulls_last`).
  - `test_value_column_renders` — the value + tier appear in the free-agent table
    HTML for a scored player and `—` for an unscored one; the cell's `title`
    carries the now/prospect/horizon breakdown.
  - `test_profile_reblends_ordering` — seed a win-now veteran (high `now_score`,
    low `prospect_score`) and a prospect (the reverse); `?profile=contend`
    orders the veteran first, `?profile=rebuild` flips them, with **no**
    recompute between requests (the read-time re-blend promise).
  - `test_unknown_profile_falls_back` — `?profile=nonsense` renders with the
    default profile rather than erroring.
  - `test_no_search_rank_tiebreak` — construct two players equal on the primary
    sort where `search_rank` would previously have decided order and assert
    `dynasty_value` decides it instead (regression guard that the tiebreak
    changed).
  - `test_free_agent_query_budget` — `assertNumQueries` unchanged by the overlay
    (subquery, not N+1).
  - `test_reserves_show_value` — a rostered player with a `PlayerValue` shows its
    value on the reserves table (covers the RosterSlot overlay path).
- **scouting** (`apps/scouting/tests/test_views.py`):
  - `test_rookie_board_orders_by_value` — within a position, the higher-value
    rookie precedes the lower one (replacing the old `search_rank` order).
  - `test_rookie_and_target_rows_show_value` — the value/tier cell renders on both
    boards; unscored players show `—`.
  - `test_scouting_query_budget` — the existing budget guard still holds with the
    overlay added.
- Manual: `make up`, open a league's free-agent board (defaults to value order,
  Value column sortable), a team's roster (reserves show value), and the rookie
  board (value-ordered within position); confirm values match a
  `PlayerValue` spot-check in the shell.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** — this is the final
PR, so this also gates the feature's completion checklist (verify all acceptance
criteria, then run the `test`/`coverage`/`quality` trio before archiving).
