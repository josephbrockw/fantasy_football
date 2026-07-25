# 03 — Surface pick values on the trades view

Feature: `007_draft-pick-valuation`

## Objective

Show each pick's value on the trades page's pick-ownership table, and confirm the
`pick_value_for` helper (PR 02) is the clean, documented interface feature `010`
will consume. Read-only presentation over the data PRs 01–02 produced.

## Scope

**In scope**
- `TradesView.get_context_data` in `apps/leagues/views.py` — attach the computed
  value to each `TradedPick` it renders, via `pick_value_for` / a batched lookup
- `apps/leagues/templates/leagues/trades.html` — a "Value" column in the
  pick-ownership table, with a graceful placeholder when a pick has no value
- View tests for the new column and the placeholder
- A one-line confirmation (in the docstring/comment) that `pick_value_for` is the
  feature-`010` interface

**Out of scope**
- Computing values (PR 02) — this PR only reads `PickValue`
- Valuing the picks *inside* trade cards (the `TradeAsset` `kind=pick` rows in
  `trade_summaries`) — deferred; the pick-ownership table is the agreed surface.
  If it drops in cheaply via the same helper, it may be added, but it is not
  required by the acceptance criteria.
- Sorting/filtering the pick table by value — not required for v1
- Any trade-*evaluation* scoring — that is feature `010`

## Implementation plan

1. **View** — in `TradesView.get_context_data`
   (`apps/leagues/views.py`, ~line 331): the `traded_picks` queryset already
   selects `original_owner`/`current_owner` for the season. Pick values key on
   `League` + pick `season` + `round` (see PR 01), so fetch the league's values
   once with the PR 02 batch helper and zip them onto the rows rather than calling
   `pick_value_for` per row (avoid N+1):

   ```python
   from apps.leagues.valuation import pick_values_for_league

   picks = list(
       TradedPick.objects.filter(league_season=season)
       .select_related("original_owner", "current_owner")
       .order_by("season", "round", "original_owner__display_name")
   )
   values = pick_values_for_league(league)  # {(season, round, slot): PickValue}
   pick_rows = [
       {"pick": p, "value": values.get((p.season, p.round, 0))}
       for p in picks
   ]
   context["pick_rows"] = pick_rows
   ```

   Keep the empty-season branch (`season is None`) returning `[]`, matching the
   current shape. Do the pairing in the view — the same logic-in-view,
   logic-light-template split `trade_summaries` and `starting_lineup` already use.

2. **Template** — `apps/leagues/templates/leagues/trades.html`, the pick-ownership
   table (lines ~72–102). Add a **Value** header cell and, in the row loop, a
   value cell. Iterate `pick_rows` instead of `traded_picks`, referencing
   `row.pick` and `row.value`:

   ```django
   <th class="px-4 py-2 font-medium">Value</th>
   ...
   {% for row in pick_rows %}
     <tr class="hover:bg-slate-900/40">
       <td class="px-4 py-2 font-medium text-slate-200">
         {{ row.pick.season }} R{{ row.pick.round }}
       </td>
       <td class="px-4 py-2 text-slate-400">{{ row.pick.original_owner }}</td>
       <td class="px-4 py-2 text-slate-300">
         {{ row.pick.current_owner }}
         {% if row.pick.current_owner.is_me %}<span class="…">me</span>{% endif %}
       </td>
       <td class="px-4 py-2 text-right tabular-nums text-slate-300">
         {% if row.value %}{{ row.value.value|floatformat:0 }}
         {% else %}<span class="text-slate-600">—</span>{% endif %}
       </td>
     </tr>
   {% endfor %}
   ```

   Bump the empty-state `colspan` from `3` to `4`. Keep the existing Tailwind
   classes and the `me` badge markup exactly as they are — this is an additive
   column. Consider a small header note (like the existing "picks that have
   changed hands" caption) that the value is a round-level baseline estimate.

3. **Interface note.** In `apps/leagues/valuation.py`, ensure the
   `pick_value_for` docstring states it is the public read interface for pick
   values, consumed by `TradesView` and (planned) feature `010`'s trade/draft
   what-if — so `010` reuses it rather than re-deriving values.

## Testing

Extend `apps/leagues/tests/test_trades_view.py` (created in feature `003`), or add
`test_trades_view_pick_values.py`, seeding via the existing league factories plus
a couple of `PickValue` rows (`slot=0`) for the league:

- `test_pick_value_column_renders` — the pick-ownership table shows a **Value**
  header and the value for a pick that has a `PickValue`.
- `test_pick_without_value_shows_placeholder` — a `TradedPick` with no matching
  `PickValue` renders the `—` placeholder, not an error.
- `test_pick_value_query_budget` — an `assertNumQueries` guard proving the batched
  `pick_values_for_league` lookup keeps the page flat regardless of pick count
  (no per-row query).
- `test_empty_pick_table_colspan` — the "no picks" empty state still spans the
  full (now 4-column) table.
- Manual: `make recompute-pick-values`, open `/league/<slug>/trades/`, confirm the
  Value column reads sensibly and unknown picks show `—`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review**. This is the last PR
in the feature — after review, run the full verification gate (`test-runner`,
`coverage-runner`, `quality-runner`), update docs (feature README; note the new
model/command in `CLAUDE.md`'s layout/PM sections if warranted), then hand off to
`pm-updater` to archive.
