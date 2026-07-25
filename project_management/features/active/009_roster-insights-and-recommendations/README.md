# 009 — Roster Insights & Recommendations

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Turn the ML arc's raw signals into a single actionable call per player, answering
the only question that matters between games: *which players do I keep, move, and
target?* A transparent rules layer maps each player — in the context of **my**
team — to one of **keep / sell-high / buy-low / target / cut / hold**, with a
plain-English rationale, by combining the three-axis dynasty valuation
(`PlayerValue` — `now_score` / `prospect_score` / `horizon`, 006), form-trend
and consistency (`PlayerSeasonMetrics`, 005), positional need or surplus
(`TeamPositionStrength`, 008), and market signal (`TrendingPlayer`). The 006
`WEIGHT_PROFILES` stance (`balanced` / `contend` / `rebuild`) is **chosen
manually** — a stance selector on the insights page, defaulting to `balanced` —
and the engine takes it as a plain parameter; there is deliberately **no
auto-selection** from the 008 outlook (how aggressively to contend or rebuild
is a judgement call, not a computed one). The same player can correctly be a
*keep* under `contend` and a *sell* under `rebuild`. The
recommendation is **explainable by construction** — every badge carries the exact
reasons it fired — so it is trustworthy enough to act on, and it feeds the
existing Targets board (a buy/target suggestion becomes a one-click `Target`).

This is the capstone of the ML arc (005–010): it consumes the upstream models and
adds no new market data of its own. It is computed **on demand**, not
materialised — see PR 01 for the justification.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A new `apps/insights/` app is in `INSTALLED_APPS`. It carries the
      recommendation engine as a **pure, deterministic function** over already-
      materialised upstream signals — there is **no** new model, migration, or
      `recompute` command (the on-demand decision is justified in PR 01's plan and
      the module docstring).
- [ ] The engine maps a `(player, my-team)` pair to exactly one `RecLabel`
      (`keep`, `sell_high`, `buy_low`, `target`, `cut`, `hold`) plus a
      `rationale` — an ordered list of human-readable reason strings naming the
      signals that fired. The rules are stated in one place and driven by
      `PlayerValue` (006) — the blended value/tier under a **manually selected**
      `WEIGHT_PROFILES` stance (passed in as a parameter, default `balanced`),
      plus the individual `now_score` / `prospect_score` axes and expiration
      risk (`horizon_seasons` / `expires_season`) — `PlayerSeasonMetrics`
      form-trend / consistency (005), `TeamPositionStrength` need/surplus at the
      player's position (008), and `TrendingPlayer` add/drop market interest.
- [ ] The engine **degrades gracefully**: when an upstream signal is missing
      (e.g. a player without a `PlayerValue` row, or before 005/006/008 syncs have
      run) it falls back to `hold` / no-recommendation rather than raising, so the
      surfaces never 500 on incomplete data.
- [ ] Recommendation badges are colour-coded and appear on the team-detail
      starting lineup and reserves rows, and on the free-agent and rookie boards,
      lazy-loaded via an `insights` HTMX widget (mirroring the Targets widget) so
      the host views/templates in `leagues`/`scouting` never import `insights`
      Python. Each badge's rationale is available on hover/expand.
- [ ] A **Roster insights** summary page at `/league/<slug>/insights/` (linked
      from the league sub-nav) lists my team's calls grouped into **sell
      candidates**, **cut candidates**, **buy targets**, and **age risks**, each
      row showing the player and its rationale.
- [ ] From a **buy** or **target** suggestion I can add the player to my Targets
      in one click (stance `acquire`, seeded with the value tier), reusing the
      existing per-league `Target` model; the control reflects the added state
      without a full page reload.
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered and no test hits the network or depends on live upstream data
      (fabricated signal inputs / rows only).

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [Recommendation engine (on-demand rules layer)](01_recommendation-engine.md) | Planned | |
| 02 | [Recommendation badges on roster & boards](02_recommendation-badges.md) | Planned | |
| 03 | [Roster insights summary & Targets integration](03_roster-insights-and-targets.md) | Planned | |

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
</content>
</invoke>
