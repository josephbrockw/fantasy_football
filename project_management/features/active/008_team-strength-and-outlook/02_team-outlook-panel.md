# 02 — Team outlook panel on team-detail & dashboard

Feature: `008_team-strength-and-outlook`

## Objective

Surface PR 01's `TeamRating` where it answers "how is my team likely to do?": a
**team outlook** panel on the team-detail page, plus a compact rating summary on
the dashboard "My teams" cards. This PR is presentation only — it consumes
`team_rating()` and adds no new computation beyond wiring it into the existing
views. League-relative strengths/gaps and the power ranking come in PR 03; this
PR shows the single-team view and states plainly that it is a roster-strength
outlook, not a standings projection.

## Scope

**In scope**
- Add the rating to `TeamDetailView.get_context_data` in `apps/leagues/views.py`.
- New partial `apps/leagues/templates/leagues/_team_outlook.html`, included on
  `team_detail.html`.
- A compact rating line on the dashboard cards
  (`apps/leagues/templates/leagues/dashboard.html`), fed by `DashboardView`.
- View/template tests.

**Out of scope**
- Per-position strength/gap *relative to the league* and the power ranking
  (PR 03) — this panel shows the team's own positional value breakdown and
  absolute score only.
- Any schedule/standings projection (PR 04).
- Changes to `ratings.py` (consume it as-is; if a rendering helper is needed,
  prefer a small formatting function in the view/module rather than reworking the
  dataclass).

## Implementation plan

1. **`TeamDetailView`** (`apps/leagues/views.py`): in `get_context_data`, after
   the existing lineup/reserves setup, add
   `context["rating"] = team_rating(self.object)` (import from
   `apps.leagues.ratings`). The view already `select_related`s the team; ensure
   the rating's roster query stays off the hot path (it is a couple of queries by
   PR 01's design).

2. **`_team_outlook.html`** partial, styled to match the existing
   `team_detail.html` panels (slate borders, `text-xs uppercase tracking-wider`
   section headers, `tabular-nums`). Render:
   - **Overall rating** as the headline number, plus dynasty capital
     (players + picks; picks show "—" when 007 absent).
   - **Positional breakdown** — a small table/bar per `positions` entry
     (position label, total value, starter share), reusing the football order.
   - **Depth** — bench and taxi depth figures.
   - **Age / window** — the weighted average age, the band counts, and the
     `window` hint rendered as a labelled pill (contend / balanced / rebuild),
     matching the existing pill styling (e.g. the "my team" badge).
   - **Unrated state** — when `rating.is_rated` is False, replace the numbers
     with a hint: "Unrated — run the dynasty valuation sync (feature 006)". When
     `rating.unvalued` > 0 but rated, show a small "N players unvalued" note.
   - A one-line disclaimer: *"Roster-strength outlook — not a standings or
     playoff projection (needs schedule data)."*

3. **Include the panel** in `team_detail.html` between the header `<dl>` and the
   "Starting lineup" section, so the outlook is the first thing seen. Keep the
   include guarded (`{% if rating %}`) for safety.

4. **Dashboard** (`DashboardView` + `dashboard.html`): the view already builds
   `my_teams`. Attach a rating to each (either annotate a lightweight
   `team.rating = team_rating(team)` in the loop, or build a parallel list of
   `(team, rating)` — prefer the parallel list to avoid mutating ORM instances).
   In the card's `<dl>`, add a "Rating" figure (and, once PR 03 lands, its power
   rank — leave a placeholder comment). Guard for the unrated case with an
   em dash.

## Testing

Extend `apps/leagues/tests/` (e.g. `test_views.py` or a new
`test_outlook_views.py`), `TestCase`, no network:

- `test_team_detail_shows_rating` — GET `team_detail`; response contains the
  overall rating and the roster-strength disclaimer.
- `test_team_detail_unrated_state` — with no `PlayerValue` data, the panel shows
  the "unrated" hint and does not 500.
- `test_team_detail_positional_breakdown` — the panel lists the league's
  `fantasy_positions` in football order with per-position values.
- `test_dashboard_card_shows_rating` — GET dashboard as a user with a "my team";
  the card renders the rating figure (and em dash when unrated).
- Reuse the roster fixtures from `test_services.py` / PR 01, injecting values via
  006's rows or by patching `ratings.player_values`.

Run narrowed: `make test ARGS="apps.leagues"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
