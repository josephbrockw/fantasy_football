# 006 — Player Dynasty Valuation

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Give every relevant player a **three-axis dynasty valuation** — not one opaque
number, but three separately-stored, separately-inspectable sub-scores that are
**blended into an overall value by tunable weights**:

1. **`now_score` (0–100)** — how much the player adds to a *winning* fantasy
   team **this season**: realised production, recent form. No age adjustment —
   this axis is purely "does he help me win now".
2. **`prospect_score` (0–100)** — how likely the player is to **break out
   later**: a pedigree prior from NFL draft capital that *decays as evidence
   accumulates*, plus usage trajectory and market signal. This is the axis that
   separates "good odds to pop" from "eating a roster spot".
3. **`horizon` (the expiration date)** — how long the player should **hold his
   current form**: expected remaining seasons from a position-specific age curve,
   stored as `horizon_seasons` and a displayable `expires_season`
   ("holds form through ~2029"), normalized to a `horizon_score` for blending.

```
value = w_now × now_score + w_prospect × prospect_score + w_horizon × horizon_score
```

Weights come from a named **`WEIGHT_PROFILES`** table — `balanced` (default),
`contend` (pushes `w_now` up, for a championship window), `rebuild` (pushes
`w_prospect` up). Because the sub-scores are **stored columns**, the blend can be
recomputed **at read time** from any profile — switching stance never requires a
recompute. The stored `value` column holds the default-profile blend so admin and
default sorts stay simple.

This retires the coarse `search_rank` tiebreak everywhere players are listed,
and remains the centrepiece of the ML arc (005–010): a `PlayerValue` model plus
a **transparent, unit-tested baseline** (`baseline-v1`) that a trained model can
later slot in behind, unchanged, through the same interface (`model_version`
discriminator + a components breakdown on every row).

## Dependencies & build order

The `NNN` is a stable global id, **not** the build order. The build order for
this slice of the arc is **011 → 005 → 006**:

- **011 `external-data-enrichment`** (build first) — provides `PlayerProfile`
  with **NFL draft capital** (`draft_year` / `draft_round` / `draft_pick`), the
  backbone of `prospect_score`'s pedigree prior. **011 PR 02 must be merged
  before 006's PR 02 is implemented.**
- **005 `player-analytics-layer`** — provides `PlayerSeasonMetrics` per
  `(player, season)`: points-per-game, recent form, consistency, usage. Feeds
  `now_score` (production) and `prospect_score` (usage trajectory). **005 must
  be merged before 006's PR 02 is implemented.** Field names used in the PR 02
  plan are the *intended* interface from 005's plan; reconcile against the
  merged model when implementing.
- **004 `stats-projections-ingestion`** (done) — `PlayerWeekStat` is the raw
  substrate 005 aggregates; 006 does not read it directly.
- **001 foundation** — reuses `Player` (`age`, `years_exp`, position),
  `TrendingPlayer` (market signal), the `SyncRun` audit log, and the free-agent /
  scouting board patterns.

PR 01 (the model) and PR 03's template plumbing have no upstream dependency and
can be built while 011/005 are in flight.

## Modeling decision (v1) — why a transparent baseline

The project rule is "decide per feature". For v1 the right call is a
**transparent, deterministic baseline**, not a trained model, because:

- **Nothing to compare against yet.** Without a working `PlayerValue` surfaced in
  the app there is no baseline to measure a model against. Ship the readable
  version first; it immediately replaces `search_rank`.
- **It is inspectable.** Every row stores its `components` (every factor behind
  each sub-score, plus raw and normalized numbers), so a surprising value is
  debuggable rather than a black box.
- **The seam is the whole point.** `model_version` + a `VALUATION_MODELS` registry
  mean a later `trained-v1` writes the *same* `PlayerValue` schema — the same
  three sub-scores, produced by learned models instead of formulas — and is read
  through the *same* overlay. Baseline and trained rows can coexist for one
  season; flipping `ACTIVE_MODEL_VERSION` switches what the app shows.

**`baseline-v1` sub-score recipes** (each factor a named, unit-tested function):

- **`now_score`** — `PlayerSeasonMetrics.points_per_game` blended with
  `recent_form_ppg` (weighted toward season-long, nudged by recent form),
  multiplied by a **durability factor** — the player's games-played rate over
  the last `DURABILITY_WINDOW` seasons (multi-season
  `PlayerSeasonMetrics.games_played`), clamped to a bounded range — so a
  producer who is chronically unavailable is worth less to a winning lineup
  *this year* than his per-game numbers suggest. Normalized 0–100 across the
  scored pool. A player with no metrics row (never played) scores 0 here —
  that is *correct*; his value lives on the other axes.
- **`prospect_score`** —
  `pedigree_prior × evidence_decay + trajectory_nudge + market_nudge`, normalized
  0–100:
  - `pedigree_prior` — from 011's draft capital: round 1 high, falling by round;
    undrafted a small constant. Missing profile → neutral floor.
  - `evidence_decay` — the prior decays with accumulated evidence (`years_exp`
    and **career games actually played**): an unproven year-1 first-rounder
    keeps ~full prior; a year-4 first-rounder with three empty seasons has
    burned it. **This decay is the pop-vs-space-eater discriminator.** Counting
    games played (not just years) is also how durability enters this axis: a
    year-3 player who missed two seasons injured has produced little *evidence*
    and keeps more prior than one who played 30 games and did nothing.
  - `trajectory_nudge` — rising usage (targets/snaps trend from 005 metrics)
    bumps the score; a prospect earning work is closer to popping.
  - `depth_chart_nudge` — from `Player.depth_chart_order` /
    `depth_chart_position` (already synced from Sleeper): a prospect sitting
    first or second on his team's depth chart has a live path to opportunity a
    fourth-stringer doesn't; clamped, and neutral when the depth chart is
    missing. Together with usage trajectory this captures *opportunity*, which
    box scores lag.
  - `market_nudge` — the small, clamped `TrendingPlayer` add-count tilt.
- **`horizon`** — from the position age curve (RB declines early, WR/TE flatter,
  QB longest): `horizon_seasons` = expected seasons of remaining form at the
  player's age (missing age → positional default), `expires_season` =
  `season + round(horizon_seasons)`, `horizon_score` = normalized 0–100.
- **`value`** — the `WEIGHT_PROFILES["balanced"]` blend of the three, stored;
  positional tiers / ranks are cut from the stored blend.

**Scoring pool (who gets a row):** the union of players with a
`PlayerSeasonMetrics` row for the season, **all rostered players** in tracked
leagues, and **recent draft classes** (draft_year within the prospect window) —
so a zero-stat rookie or taxi stash still gets a `PlayerValue` with a real
`prospect_score` instead of silently getting no row.

**Path to a trained model (concrete, not aspirational):** a later `trained-v1`
registers in `VALUATION_MODELS`, writes the same schema with feature
attributions in `components`, and is activated by pointing
`ACTIVE_MODEL_VERSION` at it — no model, command, or template change. What
"trained" means per axis (see the backlog's *Trained valuation models* item):

- **`prospect_score` → a supervised breakout probability.** The 2018+ stats
  history yields real labels ("became a top-24 positional scorer within 2
  seasons of entering the league"); a gradient-boosted or logistic classifier
  over draft capital, age, usage trajectory, depth chart, and efficiency
  replaces the hand-set prior/decay, and its per-feature attributions land in
  `components`.
- **`now_score` → empirical-Bayes shrinkage.** Per-game production on small
  samples should regress toward a positional prior (a 3-game hot streak is
  mostly noise); hierarchical shrinkage of ppg toward the position mean —
  weighted by games played — is the statistically honest version of the
  production blend. The already-ingested Sleeper **projections**
  (`PlayerWeekStat.kind="projection"`) join as an input here too.
- **`horizon` → fitted aging curves.** Replace the hand-drawn `AGE_CURVES` with
  curves estimated from the historical data itself (per-position decline in
  ppg by age, survival-style "seasons until falling out of the startable
  pool").

The baseline's hand-coded `pedigree_prior × evidence_decay` is deliberately
shaped like Bayesian updating (a prior, decayed by observed evidence) so the
trained replacement is a refinement of the same idea, not a rethink.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A `PlayerValue` model stores one row per `(player, season, model_version)`
      carrying the three sub-scores (`now_score`, `prospect_score`,
      `horizon_score`, each 0–100, plus `horizon_seasons` and `expires_season`),
      the blended `value` (0–100, default profile), a positional `tier`
      (1 = elite), a `position_rank` and `overall_rank`, a snapshot `position`,
      and a `components` JSONField capturing every intermediate so any number is
      inspectable. It has `unique_together` on that key, supporting indexes, a
      migration, and an admin registration.
- [ ] `model_version` discriminates valuation methods (default `baseline-v1`); a
      module-level `ACTIVE_MODEL_VERSION` and a `VALUATION_MODELS` registry let a
      future trained model write the same schema and be read through the same
      overlay, with baseline and trained rows coexisting for a season.
- [ ] `baseline-v1` computes the three sub-scores as specified above —
      `now_score` from 005's metrics only (production blend × a clamped
      durability factor from multi-season games-played rate); `prospect_score`
      from 011's draft capital with evidence decay (counting games actually
      played, so injury-missed time doesn't burn the prior like empty healthy
      seasons do), usage trajectory, a depth-chart opportunity nudge
      (`Player.depth_chart_order`), and clamped market nudge; `horizon` from
      the position age curve — and blends them via `WEIGHT_PROFILES`. Each
      factor is a named function with its own unit test; the compute reads only
      the DB and hits no network.
- [ ] The scoring pool includes **zero-stat prospects**: a player with no
      `PlayerSeasonMetrics` row but rostered or in a recent draft class gets a
      `PlayerValue` row with `now_score = 0` and a meaningful `prospect_score`;
      a drafted-round-1 rookie outscores an undrafted zero-stat player of the
      same age on `prospect_score`.
- [ ] `WEIGHT_PROFILES` defines at least `balanced`, `contend`, `rebuild`; the
      stored `value` uses `balanced`; the read overlay can re-blend from the
      stored sub-scores under any profile **at query time** (no recompute), and
      a contend-vs-rebuild profile flips the ordering of a win-now veteran vs a
      high-pedigree prospect in a test.
- [ ] `make recompute-values` (management command `recompute_values`) recomputes
      the pool for a season (default: the latest season with metrics),
      **idempotently** upserting `PlayerValue` on its natural key, wrapped in a
      `SyncRun` (new `valuation` kind) that records rows written; `--season`,
      `--model-version`, and `--dry-run` flags work and a re-run produces no
      duplicates.
- [ ] The shared `leagues/_player_row.html` shows each player's dynasty value and
      tier (with the sub-scores available on hover/expand), so value appears on
      the **roster, free-agent, rookie, and targets** boards from the one change.
- [ ] The free-agent board offers value-based sorting (a "Value" column, made the
      default order) and a **weight-profile selector** (`balanced` / `contend` /
      `rebuild`) that re-blends the ordering at read time; both the free-agent
      and rookie boards use real `PlayerValue` as the ordering / tiebreak **in
      place of** the coarse `search_rank`.
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered.

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [PlayerValue model & migration](01_playervalue-model.md) | Planned | Three sub-scores + blended value |
| 02 | [Baseline valuation compute, command & make target](02_baseline-valuation-compute.md) | Planned | Needs **011 PR 02** and **005** merged |
| 03 | [Surface value across boards & value sorting](03_surface-value-and-sort.md) | Planned | Read-time profile re-blend |

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
