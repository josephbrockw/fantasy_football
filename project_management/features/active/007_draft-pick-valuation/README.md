# 007 — Draft-pick valuation

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Value future draft picks — dynasty's second currency — so a pick is comparable
to a player on the same scale and shows up on the trades view (and, later, in
trade evaluation). A pick's value is the expected `PlayerValue` (feature `006`)
of the player likely drafted at that slot, estimated from historical rookie
outcomes. This is a node in the ML arc `005`–`010`: it consumes `006`'s
per-player dynasty values and produces the pick side of the ledger that feature
`010`'s trade/draft what-if will weigh players against picks.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A `PickValue` model records the expected dynasty value of a draft pick,
      keyed by `(league, season, round, slot)` — `season`/`round` matching
      `TradedPick`'s fields, `slot` being the within-round pick number with the
      sentinel `0` meaning "round-level, exact slot unknown" (the common case for
      future picks, which `/traded_picks` reports only by season + round). It has
      a `value` (float, on the same scale as `PlayerValue`), provenance fields
      (`method`, `sample_size`, `computed_at`), a `unique_together` on the key,
      a migration, and admin registration.
- [ ] A `recompute_pick_values` service computes a **baseline round-level value
      curve** from historical rookie outcomes: past rookie classes are ranked by
      each player's realised `PlayerValue`, bucketed into rounds by the league's
      pick-per-round count, and averaged across classes, so `round N` is worth the
      mean realised value of the players who land in that band. It materialises
      `PickValue` rows for the picks a league actually has and is idempotent
      (re-running updates rows in place).
- [ ] The baseline references `PlayerValue` (feature `006`) **by name** through
      its intended read interface (a per-player dynasty value); no test hits the
      network, and the compute degrades gracefully (writes nothing, no crash) when
      no `PlayerValue` rows exist yet.
- [ ] Optional **slot refinement**: when requested, the compute also writes
      per-slot rows (`slot >= 1`) from the within-class value ranks, so a known
      early pick in a round can read higher than a late one; round-level (`slot=0`)
      rows are always written as the fallback.
- [ ] `make recompute-pick-values` runs the compute (all leagues by default,
      narrowable to one league / season range via flags), wrapped in a `SyncRun`
      of a new kind that records rows written/skipped and captures failure.
- [ ] The trades view's **pick-ownership table** shows each pick's value, sourced
      through a public `pick_value_for(league, season, round, slot=0)` helper;
      picks with no computed value render a neutral placeholder rather than
      erroring, and the helper is documented as the interface feature `010`
      consumes.
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered.

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [PickValue model, migration & admin](01_pickvalue-model.md) | Planned | |
| 02 | [Baseline pick-value compute, command & make target](02_compute-and-command.md) | Planned | Depends on `006` `PlayerValue` |
| 03 | [Surface pick values on the trades view](03_trades-view-values.md) | Planned | |

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
