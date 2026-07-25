# 03 — League power ranking & positional strengths vs league

Feature: `008_team-strength-and-outlook`

## Objective

Make the rating **league-relative**: rank every team in a `LeagueSeason` into a
power ranking, and turn each team's raw positional values into strengths/gaps
measured against the rest of the league. Surface the ranking on the league
overview and fold the team's rank + positional strengths/gaps into the outlook
panel from PR 02. Still schedule-agnostic — this is comparative roster strength,
not a standings projection.

## Scope

**In scope**
- `league_power_ranking(season)` and positional strength-vs-league helpers in
  `apps/leagues/ratings.py`.
- Power-ranking surface on `LeagueOverviewView` /
  `league_overview.html`.
- Rank + strengths/gaps in `TeamDetailView` / `_team_outlook.html` and the
  dashboard card rank.
- Tests.

**Out of scope**
- Schedule ingestion and Monte-Carlo projection (PR 04).
- Re-deriving per-team ratings — reuse `team_rating()` from PR 01.

## Implementation plan

1. **`league_power_ranking(season)`** in `apps/leagues/ratings.py`: load the
   season's teams (`Team.objects.filter(league_season=season)` with
   `select_related("manager")`), compute `team_rating()` for each, sort by
   `overall` descending, and return an ordered list of a small
   `RankedTeam` dataclass:
   ```python
   @dataclass(frozen=True)
   class RankedTeam:
       team: Team
       rating: TeamRating
       rank: int          # 1-based
       percentile: float  # 0..100, higher = stronger
   ```
   Compute in a single pass over the league; **do not** call this per team from a
   loop that also calls `team_rating` again. Keep the whole-league cost to the
   per-team query budget PR 01 established × number of teams (a dozen).

2. **Positional strength vs league.** Add
   `positional_context(season)` returning, per league position, the distribution
   of teams' position values (so a team's value can be placed as a percentile /
   tercile). Then a helper `classify_positions(rating, context)` that tags each
   of a team's `PositionStrength` rows as `strength` (top third of the league for
   that position), `gap` (bottom third), or `neutral`. Document the tercile
   thresholds inline. Reuse the already-computed ratings from
   `league_power_ranking` rather than recomputing.

3. **League overview surface** (`LeagueOverviewView` + `league_overview.html`):
   the standings table already lists teams by record. Add a power-ranking view —
   simplest is an extra "Power" column (rank + score) on the existing table, kept
   independent of the record sort, plus optionally a small "Power ranking"
   ordered list above/beside standings. Build the ranking once in
   `get_context_data` (`context["power_ranking"] = league_power_ranking(season)`)
   and index it by `team.pk` in the template, or pre-join it into the existing
   `teams` iterable in the view to avoid template lookups. Preserve the current
   record/PF/PA/Players columns and the empty-state.

4. **Team detail + dashboard** (`TeamDetailView`, `_team_outlook.html`,
   dashboard): compute the league ranking for the team's season, find this team's
   `RankedTeam`, and pass `rank` / `percentile` plus the classified positions
   into the panel. In `_team_outlook.html`, render the rank ("#3 of 12"), and
   colour each positional row by its `strength`/`gap`/`neutral` tag (reuse the
   sky/amber/slate accent classes already in the templates). Add the rank figure
   to the dashboard card placeholder left in PR 02.
   - To avoid double work, have the view compute `league_power_ranking(season)`
     once and derive both the single team's context and (on the overview) the
     full table from it.

5. **Unrated / partial data** — when the league has no `PlayerValue` data, the
   power ranking still returns teams (all zero) in a stable order; the overview
   shows "—" for power and the panel keeps its PR 02 unrated hint. Ensure ties
   (equal scores, common when unrated) get a deterministic order (e.g. by
   `team.pk` or record as tiebreak) so tests are stable.

## Testing

Extend `apps/leagues/tests/test_ratings.py` and the view tests, `TestCase`, no
network. Build a `LeagueSeason` with several teams of differing roster value:

- `test_power_ranking_orders_by_overall` — strongest roster ranks 1; percentiles
  are monotonic with rank.
- `test_power_ranking_tie_break_is_deterministic` — equal scores (e.g. all
  unrated) produce a stable, documented order.
- `test_classify_positions_terciles` — a team strong at QB and weak at RB
  relative to the league is tagged `strength` / `gap` accordingly.
- `test_league_overview_shows_power_column` — GET overview; response includes the
  power rank/score for teams and preserves the record columns.
- `test_team_detail_shows_rank_and_gaps` — the panel shows "#N of M" and a
  gap-tagged position.
- `test_power_ranking_query_budget` — `assertNumQueries` confirms the ranking is
  computed in one league-wide pass, not per-team re-queries.

Run narrowed: `make test ARGS="apps.leagues"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
