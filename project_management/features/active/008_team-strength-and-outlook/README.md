# 008 — Team Strength & Season Outlook

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Answer the question **"how is my team likely to do?"** by aggregating rostered
value into a single, league-relative team rating. Roll each rostered player's
dynasty value (from feature `006_player-dynasty-valuation`) into a team score
with per-position strengths and gaps, bench/taxi depth, an age/window profile,
and total dynasty capital (players plus owned draft picks when
`007_draft-pick-valuation` is available), then rank every team in the league
into a power ranking. Surface a **team outlook** panel on the team-detail and
dashboard, and a **power ranking** on the league overview — all built on the
roster we already sync, no new external data required.

**Schedule caveat (read this first).** A true game-by-game standings/playoff
*simulation* needs matchup/schedule data this app does **not** ingest yet.
Sleeper exposes it at `GET /league/<id>/matchups/<week>`, but there is no client
method, sync, or model for it today. This feature is therefore scoped
**schedule-agnostic** for PRs 01–03 (roster-strength rating, positional needs,
league power ranking). A schedule-based projection is scoped as a clearly
separated, prerequisite-gated later PR (`04`), which must first add the client
method + matchup sync following the existing Sleeper sync pattern. Until PR 04
lands, the outlook is explicitly a **roster-strength outlook**, not a standings
projection.

## Dependencies

- **`006_player-dynasty-valuation`** (required, planned in parallel) — provides a
  per-player dynasty value. This feature plans against that intended interface:
  a `PlayerValue`-style record carrying a numeric dynasty value per `Player`.
  All access is funnelled through a single adapter (`player_values()` in
  `apps/leagues/ratings.py`) so that when 006 lands, only one import/query needs
  adjusting. Feature 008 cannot be *verified* until 006 is merged; it can be
  planned and its schedule-agnostic surfaces reviewed against the adapter.
- **`007_draft-pick-valuation`** (optional, planned in parallel) — when present,
  owned future picks (base picks per roster, adjusted by `TradedPick`
  ownership) are folded into a team's total dynasty capital through the same
  adapter. When absent, capital is players-only and the picks line reads "—".

## Modeling decision (chosen per this feature)

- **Team rating — baseline aggregation, not a learned model.** The score is a
  transparent weighted sum of rostered players' `PlayerValue`: starters weighted
  above bench, bench above taxi, grouped by position. This is explainable, has no
  training-data requirement, and composes cleanly with 006 (whose per-player
  value is where any learning lives). The **age/window profile is descriptive**
  (a team age curve + contend/rebuild hint), *not* a re-weighting — 006's dynasty
  value is already age-aware, so re-applying age here would double-count.
- **Power ranking — a deterministic sort + percentile** over the team scores. No
  model.
- **Season projection (PR 04, if built) — a simple Monte-Carlo simulation**, not
  a learned model. We have no league-level "final standings" labels to train on,
  and a transparent simulation over each team's weekly scoring distribution
  (drawn from `PlayerWeekStat` projections/variance) is explainable and
  adequate. A learned standings model is explicitly deferred.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A pure `team_rating(team)` service in `apps/leagues/ratings.py` returns a
      `TeamRating` dataclass with: an overall dynasty score; starter strength
      (sum of starters' `PlayerValue`, resolved by lineup slot via the existing
      `starting_lineup`); per-position strength (value totals for each of the
      league's `fantasy_positions`); bench and taxi depth scores; an age profile
      (value-weighted average age plus counts by age band); and total dynasty
      capital (players, plus owned picks when 007 is available). The rating is
      **derived on the fly, never stored** — no new DB table — mirroring how
      `free_agents()` treats a derived concept.
- [ ] Every `PlayerValue` (and, when present, pick-value) lookup goes through a
      single adapter in `apps/leagues/ratings.py`, so 006/007's final import path
      is changed in exactly one place. A player with no `PlayerValue` contributes
      zero and is counted as unvalued, never raising.
- [ ] `league_power_ranking(season)` returns every team in a `LeagueSeason`
      ranked by overall dynasty score, each with a 1-based rank and a percentile,
      computed in a single pass (no N+1 across the league).
- [ ] A **team outlook** panel renders on the team-detail page showing the
      overall rating, per-position strengths/gaps (a position is a *gap* when its
      value share ranks in the bottom third of the league, a *strength* in the
      top third), depth, the age/window hint, dynasty capital, and the team's
      league power rank. A compact rating + rank summary also renders on the
      dashboard "My teams" cards.
- [ ] The league overview shows a **power ranking** (rank + score column, or an
      ordered section) alongside the existing standings, without breaking the
      current record/PF/PA columns.
- [ ] Every surface degrades cleanly when 006 has not been synced (no
      `PlayerValue` rows): scores read as zero/"unrated", nothing 500s, and the
      panel shows an "unrated — run the valuation sync" hint.
- [ ] The outlook copy makes explicit that it is a **roster-strength** view, not a
      standings/playoff projection, until schedule ingestion (PR 04) exists.
- [ ] **(PR 04, only if the schedule prerequisite is taken up)** `SleeperClient`
      gains `get_league_matchups(league_id, week)` hitting
      `/league/<id>/matchups/<week>`, a `Matchup` model + idempotent
      `sync_matchups` (wrapped in a new `SyncRun` kind, following the
      transactions/stats sync pattern), and `season_projection(season)` runs a
      Monte-Carlo simulation of remaining games into projected final
      records/playoff odds. Behind its own PR and gated on the ingestion landing.
- [ ] `make test`, `make coverage`, and `make quality` all pass; all new code is
      covered and no network is hit in tests.

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [Team rating service (roster-strength, schedule-agnostic)](01_team-rating-service.md) | Planned | Depends on 006's `PlayerValue`; picks optional via 007 |
| 02 | [Team outlook panel on team-detail & dashboard](02_team-outlook-panel.md) | Planned | Builds on 01 |
| 03 | [League power ranking & positional strengths vs league](03_league-power-ranking.md) | Planned | Builds on 01–02 |
| 04 | [Schedule ingestion & Monte-Carlo season projection (prerequisite-gated)](04_schedule-projection.md) | Planned | Later / optional; adds matchup client + sync first |

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
