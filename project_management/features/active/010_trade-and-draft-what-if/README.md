# 010 — Trade & Draft What-If Evaluation

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Let me construct a **hypothetical** trade (players + picks each way) or a
**draft/add**, and immediately see the value delta plus how it moves my team's
rating and outlook — before vs after — so I can answer "how would my team change
if I made this trade, or drafted this player?" without committing anything. This
is the capstone of the ML arc **005–010**: the layer that turns the per-asset
valuations from **006** (`PlayerValue`) and **007** (`PickValue`) and the team
strength/outlook from **008** into a single, side-by-side decision view. It is
strictly **read-only** — nothing is persisted, and no `Trade` / `TradeAsset` /
`TradedPick` rows are ever written; it evaluates deals that don't exist.

### Dependencies & placement

This feature is planned against the *intended* interfaces of three features being
built in parallel; plan to their model/interface names, and if a name lands
differently, match theirs:

- **006 `player-dynasty-valuation`** — a `PlayerValue` per player (the value of
  each player on either side of a deal).
- **007 `draft-pick-valuation`** — a `PickValue` per future pick (season + round),
  keyed the way `TradedPick` is (see `apps/leagues/models.py`).
- **008 `team-strength-and-outlook`** — a team rating/outlook. This feature needs
  to recompute that rating for a *hypothetical* roster, so it depends on 008
  exposing a **pure roster-rating entrypoint** (a function that rates an
  arbitrary set of players for a `LeagueSeason`, e.g. `rate_roster(players,
  league_season)`), not only a stored rating for real rosters. If 008 only
  persists ratings for actual `Team`s, PR 01 must land that pure entrypoint in
  008's module (or coordinate for 008 to add it) — it is a hard prerequisite.

All new code lives in the **analytics app that 006–008 introduce** (referred to
here as `apps/analytics/`; match the real name). That app already imports
`apps/leagues`, so the what-if UI lives there too — `leagues` must **not** import
analytics, mirroring the one-way `scouting → leagues` coupling noted in
`apps/scouting/views.py`.

### Relationship to the "Rookie draft board" backlog item

The backlog's **Rookie draft board** (`/league/<id>/drafts`) is the tool for
*running* a live rookie draft against the scouting board. THIS feature is the
**evaluation layer** that tool (and the trade UI) can call — "what does drafting
this rookie do to my team?" — not a draft-running tool. Cross-reference it; do
not duplicate the draft flow here.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A **pure, no-DB-write** evaluation service — `apps/analytics/whatif.py` —
      exposes `evaluate_trade(proposal)` returning, for a hypothetical deal
      between two `Team`s: each side's total value (summing `PlayerValue` for
      players and `PickValue` for picks), the net value delta, and — for the
      teams involved — the team rating/outlook and positional breakdown **before
      vs after**, plus a deterministic verdict band (win / fair / loss). It writes
      no `Trade` / `TradeAsset` / `TradedPick` / any other row.
- [ ] A hypothetical deal is expressed as lightweight refs mirroring
      `TradeAsset`'s shape — a `Player` (by pk) or a pick (`season` + `round`) —
      as a list per side; FAAB is explicitly out of scope for v1.
- [ ] Team rating/outlook before/after is produced by calling **008's pure
      roster-rating entrypoint** on the hypothetical post-trade roster (players
      sent removed, players received added). Picks contribute to the **value
      ledger**, not to the current-roster rating (documented in the service).
- [ ] An interactive **what-if builder** page at `league/<slug>/what-if/` lets me
      pick the other team and the assets moving each way and — via HTMX, no full
      page reload — see both sides' totals, the delta + verdict, and my team's
      before/after rating, outlook, and positional impact. Read-only: nothing is
      persisted and leaving the page loses the scenario.
- [ ] A **draft / add** what-if mode: add a rookie (reusing the scouting rookie
      selection UI), a free agent, or a future pick to my roster and re-evaluate
      my team's rating/outlook before vs after — a one-sided evaluation that
      reuses the same service.
- [ ] The builder is reachable from the shared league sub-nav
      (`templates/_league_nav.html`), marked active on its page.
- [ ] The evaluation is **deterministic** given the upstream
      `PlayerValue` / `PickValue` / team-rating inputs; the only judgement is the
      win / fair / loss threshold band, defined as named constants in the service
      and covered by tests.
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered; no test touches the network and no test writes a trade row.

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [Trade evaluation service](01_trade-evaluation-service.md) | Planned | |
| 02 | [What-if builder UI (trade)](02_what-if-builder-ui.md) | Planned | |
| 03 | [Draft / add what-if](03_draft-what-if.md) | Planned | |

## Definition of Done

The feature is complete only when every box is checked. Then finalize the docs
and move this directory to `features/archived/`.

- [ ] All acceptance criteria verified
- [ ] All new/changed code has test coverage
- [ ] All tests pass (`make test` / `test-runner`)
- [ ] Coverage confirmed (`make coverage` / `coverage-runner`)
- [ ] Code quality confirmed (`make quality` / `quality-runner`)
- [ ] No outstanding build errors
- [ ] Documentation updated
