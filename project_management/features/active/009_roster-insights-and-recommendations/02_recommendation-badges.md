# 02 — Recommendation badges on roster & boards

Feature: `009_roster-insights-and-recommendations`

## Objective

Surface PR 01's recommendations as colour-coded badges on the screens where a
manager acts: the team-detail starting lineup and reserves, and the free-agent
and rookie boards. Badges are lazy-loaded through a new `insights` HTMX widget —
exactly the pattern the Targets widget uses — so the host templates in `leagues`
and `scouting` reference a URL rather than importing `insights` Python. Each
badge shows the label and, on expand/hover, its rationale.

## Scope

**In scope**
- `apps/insights/urls.py` + a `recommendation_widget` view.
- Templates `insights/_recommendation_badge.html` (and a tiny wrapper the widget
  renders).
- A recommendation column added to `leagues/team_detail.html` (starting lineup),
  `leagues/_reserve_tables.html` (reserves),
  `leagues/_free_agent_table.html`, and `scouting/_rookie_table.html`.
- Mounting `apps.insights.urls` in `config/urls.py`.
- View/template tests.

**Out of scope**
- The roster-insights summary page and one-click add-to-Targets (PR 03).
- The Targets board column (PR 03, alongside the add action).
- Any change to the rule engine (PR 01).

## Context: the widget must know *my* team

A recommendation is only meaningful relative to a team. Two cases:

- **Team detail** — the context team is the team being viewed (`team.pk`).
- **Boards** (free agents, rookies) — the context is **my** team in that league
  (`Manager.is_me` → the current `LeagueSeason`'s `Team`), since a board is about
  my next move. Resolve it once from the league slug; if I have no team in the
  league, the widget renders nothing (no badge), never an error.

The widget endpoint therefore keys on `(slug, player_pk)` and resolves the
context team itself (my team in that league), with an optional `team` query param
so the team-detail screen can pass the exact team it is showing. This keeps the
lazy-load URL uniform across surfaces.

## Implementation plan

1. **View — `apps/insights/views.py`.** Add
   `recommendation_widget(request, slug, pk)`:
   - `league = get_object_or_404(League, slug=slug)`.
   - Resolve the context team: `int(request.GET.get("team"))` if present and it
     belongs to this league, else `Team.objects.filter(
     league_season=league.current_season, manager__is_me=True).first()`.
   - If there is no context team, render the badge template with `rec=None`
     (renders empty). Otherwise
     `recs = gather_recommendations(team, [player]);
     rec = recs.get(player.pk)` and render `insights/_recommendation_badge.html`
     with `rec` and `LABEL_STYLE`.
   - Mirror `scouting.views.target_widget`'s shape (a plain function view, not a
     CBV).

2. **URLs.** `apps/insights/urls.py` with `app_name = "insights"` and
   `path("league/<slug:slug>/player/<int:pk>/recommendation/",
   views.recommendation_widget, name="recommendation_widget")`, matching the
   scouting URL layout. Mount it in `config/urls.py` with
   `path("", include("apps.insights.urls"))` alongside the scouting/leagues
   includes.

3. **Badge template — `apps/insights/templates/insights/_recommendation_badge.html`.**
   A collapsed `<details>` (same idiom as `_target_controls.html`, which survives
   the tables' `overflow-x-auto`):
   - Summary: a pill coloured by `LABEL_STYLE[rec.label]` showing the label's
     display text (Keep / Sell high / Buy low / Target / Cut / Hold). When
     `rec` is falsy, render nothing.
   - Expanded: the `rec.rationale` lines as a short list, so the "why" is one
     click away and the recommendation stays trustworthy.
   - Self-contained id `#rec-{{ league.pk }}-{{ player.pk }}` so it can be
     swapped independently.

4. **Team detail — `leagues/team_detail.html`.** Add an "Insight" `<th>` next to
   the existing "Target" column, and in the lineup row (and the empty-slot
   `colspan`) add a `<td>` that lazy-loads the widget, mirroring the Targets
   `hx-get ... hx-trigger="load"` block already present:
   ```
   <div hx-get="{% url 'insights:recommendation_widget' team.league_season.league.slug row.slot.player.pk %}?team={{ team.pk }}"
        hx-trigger="load" hx-swap="innerHTML">…</div>
   ```
   Bump the empty-slot `colspan` (currently `8`) and the "no lineup" `colspan`
   (currently `9`) by one to keep the table aligned.

5. **Reserves — `leagues/_reserve_tables.html`.** Add the matching "Insight"
   header and per-row lazy widget `<td>` (pass `?team={{ team.pk }}`). Check this
   fragment's own header/`colspan` counts and adjust. (Read the file first; it
   was not modified in this PR's exploration.)

6. **Free-agent board — `leagues/_free_agent_table.html`.** Add an "Insight"
   column; the widget resolves *my* team from the slug (no `team` param needed).
   Confirm the board already renders `_player_row.html` per row and add the `<td>`
   after it; extend any group/empty `colspan`.

7. **Rookie board — `scouting/_rookie_table.html`.** Same column addition, for
   parity on the draft board.

8. **Keep the host apps import-free of `insights`.** No Python in `leagues` or
   `scouting` imports `insights`; the coupling is a template URL only (the same
   one-way rule the Targets widget follows). Note this in the PR description.

## Testing

Add `apps/insights/tests/test_views.py` (a `TestCase`; no network):

- Fixtures: a `League` + current `LeagueSeason`, a `Manager(is_me=True)` with a
  `Team`, a `Player` on that team's roster, plus fabricated upstream signal rows
  where those models are importable (else lean on the trending/roster-only path,
  as in PR 01's adapter tests).
- `test_widget_renders_badge_for_my_team` — GET the widget URL for a rostered
  player returns 200 and the expected label text.
- `test_widget_with_explicit_team_param` — passing `?team=<pk>` uses that team's
  context.
- `test_widget_no_my_team_renders_empty` — a league where I have no team returns
  200 and no badge pill (empty body), never a 500.
- `test_widget_unknown_player_404` — a bogus `pk` 404s.
- `test_rationale_present_in_expanded_markup` — the rendered HTML contains the
  recommendation's rationale text.
- Template smoke: request `team_detail` and the free-agent board pages and assert
  the "Insight" `hx-get` to `insights:recommendation_widget` is present in the
  markup (the lazy-load hook wired correctly), without asserting the async badge
  content.

Run narrowed: `make test ARGS="apps.insights"` and
`make test ARGS="apps.leagues"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
