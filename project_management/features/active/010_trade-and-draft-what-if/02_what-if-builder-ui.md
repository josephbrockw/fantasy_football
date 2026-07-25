# 02 — What-if builder UI (trade)

Feature: `010_trade-and-draft-what-if`

## Objective

An interactive, read-only what-if builder at `league/<slug>/what-if/`: pick the
other team, add the players and picks moving each way, and — via HTMX, no full
page reload — see both sides' totals, the value delta + verdict, and my team's
before/after rating, outlook, and positional impact. Nothing is persisted;
leaving the page loses the scenario. This PR wires PR 01's `evaluate_trade` to a
page and the shared league sub-nav.

## Scope

**In scope**
- `WhatIfBuilderView` (the page) and an HTMX `WhatIfEvaluateView` (returns just
  the result + current-scenario fragment) in `apps/analytics/views.py`.
- URLs under `league/<slug>/what-if/` in the analytics app's `urls.py`.
- Templates `analytics/what_if.html` + partials `_whatif_result.html` and an
  asset-picker partial, in the HTMX form idiom used by
  `apps/scouting/templates/scouting/_target_controls.html`.
- A **What-if** link in `templates/_league_nav.html`, active on this page.
- View tests.

**Out of scope**
- The draft/add mode and rookie selection (PR 03) — this PR is two-team trades.
- Any persistence, and any change to `apps/leagues` (it must not import
  analytics). FAAB assets (v1 is players + picks).
- The evaluation math itself (PR 01) — this PR only assembles a `TradeProposal`
  and renders the returned `TradeEvaluation`.

## Implementation plan

The page follows the per-league, HTMX-fragment shape of the scouting boards
(`apps/scouting/views.py` + templates): a full page that renders an initial
(empty) state, and HTMX endpoints that re-render just a fragment as the scenario
changes — the same `hx-post` / `hx-target` / `hx-swap="outerHTML"` pattern as
`_target_controls.html`. Because the scenario is **not** persisted, it lives in
the form: every asset chip is a hidden input, and re-evaluation posts the whole
form back.

1. **URLs** in `apps/analytics/urls.py` (create `app_name = "analytics"` if
   006–008 haven't; otherwise append), mounted at the project root like scouting
   so the `league/<slug>/` prefix lines up:
   ```python
   path("league/<slug:slug>/what-if/", views.WhatIfBuilderView.as_view(), name="what_if"),
   path("league/<slug:slug>/what-if/evaluate/", views.WhatIfEvaluateView.as_view(), name="what_if_evaluate"),
   ```
   Confirm the analytics urls are `include()`d in `config/urls.py` (scouting's
   are the template to copy).

2. **`WhatIfBuilderView(TemplateView)`** — `template_name =
   "analytics/what_if.html"`. Context:
   - `league = get_object_or_404(League, slug=self.kwargs["slug"])` and
     `season = league.current_season` (degrade to an empty state when `None`,
     like `LeagueOverviewView`).
   - `my_team` — `Team.objects.filter(league_season=season, manager__is_me=True)`
     (the perspective team; the deltas frame from here).
   - `other_teams` — the season's other `Team`s (for the "trade with" picker),
     `select_related("manager")`, excluding `my_team`.
   - `rosterable` — for the asset pickers: my team's players and the selected
     other team's players come from `RosterSlot` (reuse the
     `select_related("player")` pattern from `apps/leagues/views.py`). Pick
     options for each side come from `TradedPick` / the league's future-pick set
     as 007 exposes it.
   - No result on first load — the result fragment renders empty until the user
     adds assets.

3. **`WhatIfEvaluateView(TemplateView)`** — the HTMX endpoint,
   `template_name = "analytics/_whatif_result.html"`. It:
   - Reads the scenario from the POST (see step 5): `other_team_id`, and repeated
     `a_send` / `b_send` values encoding each asset as `player:<pk>` or
     `pick:<season>:<round>` (parse defensively; ignore anything unrecognised,
     matching how `set_target` validates input).
   - Builds a `TradeProposal(team_a=my_team, team_b=other_team, a_sends=…,
     b_sends=…)` and calls `evaluate_trade` from PR 01.
   - Renders `_whatif_result.html` with the `TradeEvaluation`. Writes nothing.

4. **`analytics/what_if.html`** (extends `base.html`, reuses existing Tailwind
   classes and the sub-nav in `{% block nav %}`):
   - The **season/other-team picker** and the two **asset columns** ("You send" /
     "You receive") live inside one `<form>` that `hx-post`s to
     `what_if_evaluate` with `hx-target="#whatif-result"`,
     `hx-swap="outerHTML"`, `hx-trigger="change"` — so any add/remove
     re-evaluates immediately, exactly like `_target_controls.html`'s change-
     triggered form. Include `{% csrf_token %}`.
   - Each chosen asset is a hidden `<input name="a_send" value="player:123">` (or
     `pick:2027:1`) rendered as a removable chip; a `<select>`/typeahead adds
     one. Keep it logic-light — server re-renders the chips in the result
     fragment so client JS stays minimal (HTMX only).
   - A `#whatif-result` container holding the initial empty state.
   - Empty states for "this league has no synced season" and "you don't have a
     team in this league", mirroring `LeagueOverviewView` / the trades page.

5. **Scenario encoding.** Since nothing is persisted, the form *is* the state.
   Each side is a list of hidden inputs; `WhatIfEvaluateView` reads
   `request.POST.getlist("a_send")` / `getlist("b_send")` and the selected
   `other_team_id`, parses `player:<pk>` / `pick:<season>:<round>` into PR 01's
   `PlayerRef` / `PickRef`, and drops malformed entries. Round-trip the parsed,
   validated chips back in the result fragment so the two stay in sync.

6. **`analytics/_whatif_result.html`** — renders the `TradeEvaluation`:
   - Two side panels: each asset with its value (reuse the player-name snippet /
     styling used elsewhere so rows look native), the side `total`, and any
     `unvalued` flags.
   - The headline: net value + the **verdict** badge (win = emerald / fair =
     slate / loss = red, matching the acquire/avoid badge palette in
     `_target_controls.html`).
   - **My team before → after**: rating, outlook, and the positional-impact rows
     from `team_a_delta`; show the rival's `team_b_delta` more compactly.
   - The current chips (echoed hidden inputs) so the surrounding form stays
     authoritative after each swap.

7. **Sub-nav.** Add a **What-if** link to `templates/_league_nav.html` alongside
   the existing overview / free agents / rookies / targets / trades links:
   ```django
   <a href="{% url 'analytics:what_if' league.slug %}"
      class="{% if active == 'what_if' %}text-sky-300{% else %}hover:text-slate-200{% endif %}">What-if</a>
   ```
   Set `active == 'what_if'` on this page.

## Testing

Add `apps/analytics/tests/test_whatif_views.py` (`TestCase` + Django test
client). Seed a `LeagueSeason`, my `Team` (`Manager.is_me=True`) and a rival with
a few rostered players, and fixture `PlayerValue` rows so the evaluation returns
real numbers. Patch/stub 008's rater as in PR 01 for determinism.

- `test_builder_renders` — 200; the other-team picker and both empty asset
  columns render; the sub-nav includes the active What-if link.
- `test_evaluate_returns_result_fragment` — POST a scenario (a player each way);
  the fragment shows both totals, the net value, and a verdict badge.
- `test_before_after_shown` — my team's before and after rating/positional rows
  appear for a non-trivial deal.
- `test_malformed_asset_ignored` — a garbage `a_send` value is dropped, no 500.
- `test_no_persistence` — after evaluating, `Trade` / `TradeAsset` /
  `TradedPick` counts are still 0 (read-only guarantee end-to-end).
- `test_empty_states` — a league with no season, and a league where I have no
  team, each render an empty state rather than erroring.
- `test_query_budget` — an `assertNumQueries` guard on the evaluate endpoint so
  the value/rating lookups stay batched.
- Manual: `make up`, sync a league + valuations, open `/league/<slug>/what-if/`,
  build a deal, and confirm the result updates live without a reload and nothing
  new appears on the trades page.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or PR 03 is started.
