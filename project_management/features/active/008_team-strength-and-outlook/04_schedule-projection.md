# 04 — Schedule ingestion & Monte-Carlo season projection (prerequisite-gated)

Feature: `008_team-strength-and-outlook`

## Objective

Turn the roster-strength outlook into an actual **season projection** — projected
final records and playoff odds — by first ingesting the matchup/schedule data the
app does not have today, then simulating the remaining games. This is a **clearly
separated, later PR** because it requires a new Sleeper endpoint, a new model, and
a new sync. It should only be picked up once PRs 01–03 are merged and the
schedule prerequisite is genuinely wanted. If the prerequisite is not taken up,
the feature still ships as a complete roster-strength outlook (01–03).

## Prerequisite (do this part first, and consider splitting it out)

Sleeper exposes per-week matchups at `GET /league/<league_id>/matchups/<week>`,
returning one entry per roster with `roster_id`, `matchup_id` (teams sharing a
`matchup_id` played each other), `points`, and `starters`. **None of this is
ingested today** — there is no client method, model, or sync. This must be added
following the existing pattern before any projection is possible. It is large
enough that it may warrant being its own PR (call it `04a`) ahead of the
simulation (`04b`); split if the diff is big.

### Schedule ingestion steps

1. **Client method** — add `get_league_matchups(league_id, week)` to
   `apps/sleeper/client.py`, hitting `/league/<id>/matchups/<week>`, returning a
   list (Sleeper returns `[]` for a week with no data — tolerate it, like the
   existing endpoints). Add a `MatchupSource` `Protocol` (mirroring
   `TransactionSource` / `StatsSource`) and fold it into `SleeperAPI`.
2. **Model** — `Matchup` in `apps/leagues/models.py`, hanging off `LeagueSeason`
   (like `Trade`), one row per `(league_season, week, roster_id)` with
   `matchup_id`, `points` (Decimal), and the resolved `Team` FK. Add a
   `unique_together` idempotency key and migration. Follow the `Trade`/`TradedPick`
   docstring style noting Sleeper's grain gotchas (roster_id is season-scoped).
3. **Sync** — `sync_matchups` in `apps/leagues/services.py` (or a new
   `apps/leagues/matchups.py`), iterating weeks 1..N for a season, upserting rows
   idempotently, wrapped in a **new `SyncRun.Kind.MATCHUPS`** (extend the choices
   + migration, as feature 004 did for `STATS`). Skip/aggregate cleanly and never
   half-record a run.
4. **Command + Make target** — `apps/leagues/management/commands/sync_matchups.py`
   and a `make sync-matchups` target mirroring `sync-league` / `sync-transactions`.
5. **Tests** — client test with a fake session (no network), sync idempotency
   test, model unique-together test — all `TestCase`, mirroring
   `test_transactions.py` / the stats sync tests.

## Projection (schedule-based)

Only after ingestion exists:

6. **`season_projection(season)`** in `apps/leagues/ratings.py` (or a dedicated
   `apps/leagues/projections.py`). A **simple Monte-Carlo simulation**, chosen
   over a learned model because there is no league-level "final standings" label
   set to train on and a transparent simulation is explainable:
   - Determine played vs remaining weeks from `Matchup` rows and the league's
     schedule length (`settings`).
   - Model each team's weekly score as a distribution: mean from the team's
     `PlayerWeekStat` **projections** for its current starters (feature 004 data),
     variance from that team's realised weekly `points` spread (from ingested
     `Matchup` rows / `PlayerWeekStat` actuals). Fall back to a league-wide
     variance when a team has too few games.
   - Simulate the remaining schedule `N` times (e.g. 5,000), tally wins → final
     records, seed by the league's playoff settings, and produce per-team
     **playoff odds** and a projected final record. Keep `N` and the RNG seed
     configurable so tests are deterministic (seed the RNG).
   - Return a `SeasonProjection` dataclass (per-team: projected wins, playoff
     probability, mean/median final rank).

7. **Surface** — add a projected-record / playoff-odds column to the power
   ranking on `league_overview.html`, and a "Projected finish" line to the
   outlook panel, **only when matchup data exists**. When it doesn't, keep the PR
   01–03 roster-strength wording and hide the projection cleanly.

## Testing

`TestCase`, no network throughout. Seed the Monte-Carlo RNG for determinism.

- Client: `get_league_matchups` parses a fake payload and tolerates `[]`.
- Sync: idempotent re-run over the same weeks makes no duplicate `Matchup` rows;
  a `SyncRun` of the new kind records counts; a mid-run failure leaves no
  half-record.
- Model: `Matchup` unique-together enforced; cascade on `LeagueSeason` delete.
- Projection: with a fixed seed and a hand-built two-team schedule, projected
  wins and playoff odds match expected values; a team with no projection data
  falls back to league variance without erroring; played weeks are not
  re-simulated.
- View: overview/outlook show the projection when matchups exist and fall back to
  the roster-strength view when they do not.

Run narrowed: `make test ARGS="apps.leagues"` (and `apps.sleeper` for the client
test).

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete`. Given its size, strongly consider landing the ingestion
prerequisite (`04a`) and the simulation (`04b`) as two separate reviews.
