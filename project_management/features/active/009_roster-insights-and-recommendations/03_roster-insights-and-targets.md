# 03 — Roster insights summary & Targets integration

Feature: `009_roster-insights-and-recommendations`

## Objective

Give the recommendations a home of their own — a **Roster insights** page for my
team in a league that groups the calls into sell candidates, cut candidates, buy
targets, and age risks — and close the loop with the Targets board: a buy/target
suggestion becomes a `Target` (stance `acquire`) in one click, and the Targets
board shows the recommendation alongside each stance. This turns "here's the
call" into "acted on."

## Scope

**In scope**
- An `insights.views.RosterInsightsView` at `/league/<slug>/insights/` + its
  template.
- A `add_target_from_recommendation` endpoint (one-click acquire).
- The Insight column on the Targets board (`scouting/_targets_table.html`),
  reusing the PR 02 widget.
- A league sub-nav link to the insights page (`templates/_league_nav.html`).
- View/endpoint tests.

**Out of scope**
- Changes to the rule engine (PR 01) or the badge widget (PR 02) beyond reuse.
- Any new upstream model.

## Implementation plan

1. **Buy-candidate selection.** The insights page needs a bounded candidate pool
   for the buy side. Reuse the existing derivations rather than re-deriving:
   - **Sell / cut / keep / age-risk** come from **my roster** —
     `RosterSlot.objects.filter(team=my_team).select_related("player")`.
   - **Buy targets / buy-low** come from a capped candidate pool: the top slice
     of `leagues.views.free_agents(season, ...)` (already ordered, already
     league-position-filtered) plus, optionally, rival-rostered players — capped
     (e.g. top 50) so the page computes a bounded set. Rivals can be deferred to
     a follow-up; free agents alone satisfy the "buy targets" criterion.

2. **`RosterInsightsView` — `apps/insights/views.py`.** A `TemplateView` at
   `/league/<slug:slug>/insights/`:
   - Resolve `league`, `season = league.current_season`, and
     `my_team = Team.objects.filter(league_season=season,
     manager__is_me=True).first()`. If `my_team` is `None`, render an empty state
     ("no team in this league yet").
   - `roster_players = [slot.player for slot in my_team.roster_slots...]`;
     `buy_players = list(free_agents(season, ...)[:CANDIDATE_CAP])`.
   - `recs = gather_recommendations(my_team, roster_players + buy_players)`
     (PR 01's adapter — one pass, my-team context).
   - Bucket by `RecLabel` into the four sections:
     - **Sell candidates** → `SELL_HIGH`
     - **Cut candidates** → `CUT`
     - **Buy targets** → `TARGET` + `BUY_LOW`
     - **Age risks** → any roster player whose `SignalInputs.age_risk` is set
       (surface the risk even when the headline label is `KEEP`/`HOLD`; take this
       from the `Recommendation`'s retained inputs).
   - Pass `LABEL_STYLE` and, for buy rows, the value tier (to seed the target).

3. **Template — `apps/insights/templates/insights/roster_insights.html`.**
   Extend `base.html`, include `_league_nav.html` with `active="insights"`. Four
   sections, each a small table of `_player_row.html` rows plus a rationale cell
   (reuse the badge's rationale rendering) — matching the visual language of
   `scouting/_targets_table.html`. Empty sections render a muted "nothing here"
   line. On each **buy** row, render the one-click add control (step 4).

4. **One-click add to Targets — `add_target_from_recommendation`.** A
   `@require_POST` view
   `path("league/<slug>/player/<int:pk>/target-from-rec/", ...,
   name="add_target_from_rec")`:
   - `Target.objects.update_or_create(player=player, league=league,
     defaults={"stance": Target.Stance.ACQUIRE, "tier": <value tier or None>,
     "priority": Target.Priority.MEDIUM})` — `insights` importing
     `scouting.models.Target` is a safe one-way dependency (scouting never imports
     insights).
   - Return a small "✓ added to Targets" confirmation snippet (an
     `insights/_added_to_targets.html` partial) that `hx-swap="outerHTML"`
     replaces the button with, so the action reflects without a page reload.
   - The button on each buy row:
     `hx-post="{% url 'insights:add_target_from_rec' league.slug player.pk %}"`
     with `{% csrf_token %}`, mirroring the scouting forms' HTMX idiom.
   - Rationale for a dedicated endpoint over reusing `scouting:set_target`: that
     view returns the full `_target_controls.html` (needs the annotated player +
     control context) and is geared to the inline editor; a purpose-built
     endpoint keeps the insights page's one-click UX simple and its response
     small.

5. **Targets board Insight column — `scouting/_targets_table.html`.** Add an
   "Insight" `<th>` and a per-row `<td>` lazy-loading the PR 02 widget
   (`insights:recommendation_widget`, resolving my team from the slug). Extend the
   group-header and empty-row `colspan` (currently `9`) by one. This is a
   template-only change — `scouting` still imports no `insights` Python.

6. **Nav — `templates/_league_nav.html`.** Add an `Insights` link
   (`insights:roster_insights`) with an `active == 'insights'` highlight,
   alongside Free agents / Rookies / Targets / Trades. Update the nav's docstring
   comment to list the new key.

7. **URLs.** Extend `apps/insights/urls.py` with the `roster_insights` and
   `add_target_from_rec` routes (the include is already mounted from PR 02).

## Testing

Extend `apps/insights/tests/test_views.py` (a `TestCase`; no network):

- Fixtures as in PR 02, plus a couple of free-agent `Player`s and fabricated
  upstream signals chosen to produce one of each label (where the upstream models
  are importable; otherwise assert the buckets that the roster/trending-only path
  can produce and skip the value/analytics-dependent buckets).
- `test_insights_page_buckets` — the page groups players into Sell / Cut / Buy /
  Age-risk correctly given the fabricated signals.
- `test_insights_no_my_team_empty_state` — a league where I have no team renders
  the empty state, 200 not 500.
- `test_add_target_from_rec_creates_acquire` — POST creates a `Target` with
  stance `acquire` (tier seeded from value), and re-POST is idempotent
  (`update_or_create`).
- `test_add_target_from_rec_returns_confirmation` — the response contains the
  "added" confirmation markup.
- `test_add_target_from_rec_requires_post` — GET is rejected (405).
- `test_targets_board_has_insight_column` — the Targets board markup includes the
  `insights:recommendation_widget` `hx-get` hook.
- `test_nav_links_insights` — a league page's nav renders the Insights link.

Run narrowed: `make test ARGS="apps.insights"`,
`make test ARGS="apps.scouting"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review**. When all three PRs
are `Complete`, run the completion gate (`test-runner` / `coverage-runner` /
`quality-runner`), verify the acceptance criteria, update docs, and hand archival
to `pm-updater`.
</content>
