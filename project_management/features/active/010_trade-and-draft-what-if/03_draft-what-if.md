# 03 — Draft / add what-if

Feature: `010_trade-and-draft-what-if`

## Objective

Add a **one-sided** what-if to the builder: pick a rookie (reusing the scouting
rookie selection), a free agent, or a future pick, "add" it to my roster, and see
my team's rating / outlook / positional impact **before vs after** — the
"how would drafting this player change my team?" question. This reuses PR 01's
service (a degenerate one-sided proposal) and PR 02's page (a second mode), and
it is the evaluation layer the backlog's rookie-draft-board tool can later call.

## Scope

**In scope**
- A one-sided helper `evaluate_add(team, adds)` in `apps/analytics/whatif.py`
  built on PR 01's seam (`a_sends=[]`, added assets on the receiving side, no
  counterparty team).
- A **Draft / add** mode on the what-if page: a single "add these" picker (rookie
  / free agent / pick) and a before→after result for my team only.
- Rookie selection reusing the scouting rookie source (`rookie_players` /
  `_with_target_overlay` from `apps/scouting/views.py`) so the candidate list
  matches the rookie board.
- View + template wiring and tests.

**Out of scope**
- Running an actual draft or the `/league/<id>/drafts` board — that is the
  separate **Rookie draft board** backlog item. This PR only *evaluates* an add;
  it links the concept, it does not build the draft flow.
- Removing/dropping a player to make room (a pure add; roster-size handling, if
  008's rater cares, is 008's concern and noted below).
- Any persistence.

## Implementation plan

1. **`evaluate_add` in `apps/analytics/whatif.py`.** A thin wrapper over the
   seam PR 01 left:
   ```python
   @dataclass
   class AddEvaluation:
       team: Team
       added: SideValuation          # what's being added (players + picks)
       team_delta: TeamDelta         # my team before → after
   def evaluate_add(team, adds: list[Asset]) -> AddEvaluation: ...
   ```
   Internally build `TradeProposal(team_a=team, team_b=None, a_sends=[],
   b_sends=adds)` and reuse the existing valuation + `rate_roster` path, returning
   the one relevant side + `team_a_delta`. No new math — it's composition, so it
   stays deterministic and write-free. Note inline that a pure *add* only grows
   the rated player set; if 008's rater assumes a fixed roster size, document that
   the "after" is an over-full roster by design (we're asking "if this player were
   mine", not "who I'd cut").

2. **Candidate sources.** Reuse existing queries rather than re-deriving:
   - **Rookies** — `rookie_players(league)` from `apps/scouting/views.py` (the
     same incoming-rookie set the rookie board shows; analytics may import
     scouting the same way scouting imports leagues, or lift the small
     `years_exp=0` query — prefer importing to avoid drift).
   - **Free agents** — `free_agents(season)` from `apps/leagues/views.py`.
   - **Picks** — the league's future picks as 007 exposes them.
   Overlay `PlayerValue` so each candidate shows what it's worth before it's added.

3. **Page mode toggle.** Extend `analytics/what_if.html` (PR 02) with a
   mode switch — **Trade** (two teams, PR 02) vs **Draft / add** (one picker).
   Keep it in the same HTMX form idiom: the draft mode is a single "add" column
   (hidden `add` inputs encoded like PR 02's `player:<pk>` / `pick:<season>:<round>`)
   posting to a new endpoint. Simplest split: a `WhatIfAddView` (HTMX,
   `template_name = "analytics/_whatif_add_result.html"`) alongside the PR 02
   endpoints, so each mode stays a small, single-purpose view.

4. **URLs** in `apps/analytics/urls.py`:
   ```python
   path("league/<slug:slug>/what-if/add/", views.WhatIfAddView.as_view(), name="what_if_add"),
   ```

5. **`WhatIfAddView`.** Reads my `Team` for the league's current season, parses
   `request.POST.getlist("add")` into `PlayerRef` / `PickRef` (reusing PR 02's
   parser — factor it into a shared `_parse_assets` helper in this PR if it isn't
   already), calls `evaluate_add(my_team, adds)`, and renders
   `_whatif_add_result.html`: the added assets + their value, then my team's
   before → after rating, outlook, and positional rows. Writes nothing.

6. **`analytics/_whatif_add_result.html`.** Mirror `_whatif_result.html` but
   one-sided: an "adding" panel and my before→after block. Reuse the same verdict
   palette only where it makes sense (an add has no counterparty, so lead with the
   rating/positional change, not a win/fair/loss badge — a pure add is almost
   always a nominal "gain"; the useful signal is *how much* and *where*).

7. **Cross-reference the backlog.** Add a short note in the feature README's
   backlog section (already present) is enough; optionally leave a one-line
   comment in `WhatIfAddView` pointing at the future rookie-draft-board tool as
   the intended caller of `evaluate_add`.

## Testing

Add cases to `apps/analytics/tests/test_whatif.py` and
`apps/analytics/tests/test_whatif_views.py`.

Service (`test_whatif.py`):
- `test_evaluate_add_before_after` — adding a valued rookie raises my team's rated
  player set by exactly that player and reports a positive `rating_change`.
- `test_evaluate_add_pick_valued_not_rostered` — adding a pick shows its value but
  leaves the rated player set unchanged (consistent with PR 01's pick rule).
- `test_evaluate_add_no_writes` — no `Trade`/`TradeAsset`/`TradedPick` rows appear.

Views (`test_whatif_views.py`):
- `test_add_mode_renders` — the Draft/add picker renders with rookie + free-agent
  candidates.
- `test_add_evaluate_fragment` — POST an add; the fragment shows the added asset's
  value and my before→after rating/positional rows.
- `test_add_rookie_source_matches_board` — the rookie candidates equal
  `rookie_players(league)` (no drift from the rookie board).
- `test_add_malformed_ignored` — garbage `add` values are dropped, no 500.
- Manual: `make up`, open `/league/<slug>/what-if/`, switch to Draft/add, add a
  top rookie, and confirm my team's rating and the relevant position move, with
  nothing persisted.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review**. This is the last
PR in the feature — after review, run the full verification gate (`test-runner`,
`coverage-runner`, `quality-runner`), then hand off to `pm-updater` to archive.
