# 01 — Team rating service (roster-strength, schedule-agnostic)

Feature: `008_team-strength-and-outlook`

## Objective

Add a pure, well-tested computation layer that turns a `Team`'s roster into a
`TeamRating`: an overall dynasty score, starter strength by lineup slot,
per-position strength, bench/taxi depth, an age/window profile, and total
dynasty capital. No views, no templates, no new DB table — the rating is derived
on demand (like `free_agents()`), and every player/pick value is read through a
single adapter so feature `006`/`007`'s final import path is isolated to one
place. This is the substrate PRs 02–03 render and rank.

## Scope

**In scope**
- New module `apps/leagues/ratings.py` with the `TeamRating` dataclass and
  `team_rating(team)`.
- A single value adapter (`player_values(...)`, and pick-value access) in that
  module, wrapping feature 006's `PlayerValue` (and 007's pick values).
- Unit tests under `apps/leagues/tests/`.

**Out of scope**
- Any template, view, or URL change (PR 02).
- League-wide ranking / positional strength-vs-league (PR 03).
- Schedule ingestion or any standings/playoff projection (PR 04).
- Building `PlayerValue` itself — that is feature 006. This PR consumes it.

## Dependency note (006 / 007)

Feature 006 (planned in parallel) delivers `apps.players.models.PlayerValue`:
one row per `(player, season, model_version)`, with a `value` (0–100, normalized
across the pool), `position`, `tier`, and a `components` JSON. The app reads
whichever `model_version` an `ACTIVE_MODEL_VERSION` setting names (default
`"baseline-v1"`). Read it through **one** helper so the query lives in a single
place:

```python
# apps/leagues/ratings.py
def player_values(
    players: Iterable[Player], *, season: int, model_version: str | None = None
) -> dict[int, float]:
    """Map Player.pk -> dynasty value for the active model, 0.0 when unvalued.

    The single choke point over feature 006's PlayerValue. Queries
    PlayerValue.objects.filter(player__in=..., season=season,
    model_version=model_version or ACTIVE_MODEL_VERSION).values_list(
    "player_id", "value"). Everything downstream keys on pk; adjust to 006's
    final field names here and nowhere else. The season is the LeagueSeason's
    integer season.
    """
```

Do not stub a `PlayerValue` model in this feature. If 006 has not merged when
this PR is implemented, the helper returns all-zero (guarded by a
`try/except ImportError` or a feature check) so the module imports and tests for
the aggregation math can run against injected values.

Feature 007 (parallel) delivers `apps.leagues.models.PickValue`: keyed on
`(league, season, round, slot)`, `value` on the **same 0–100 scale** as
`PlayerValue`, with `slot=0` the "round-level, exact slot unknown" sentinel that
matches how `TradedPick` reports future picks. `owned_picks_capital` (step 4)
consumes it through the same isolate-the-import pattern.

## Design decisions

- **Derived, not stored.** A dozen teams is tiny; recomputing from
  `RosterSlot` + `PlayerValue` is cheap and can never go stale. Storing a rating
  would just be a cache to invalidate — the same reasoning the codebase applies
  to `free_agents()`.
- **Weighted aggregation.** Overall score = starter value × 1.0 + bench value ×
  configurable weight (default 0.4) + taxi value × lower weight (default 0.25).
  Weights live as module constants (`STARTER_WEIGHT`, `BENCH_WEIGHT`,
  `TAXI_WEIGHT`) with a comment justifying the defaults. IR is excluded from the
  active score but counted in total dynasty capital.
- **Age is descriptive, not a re-weight.** 006's value is already age-aware;
  re-applying age would double-count. The age profile is a value-weighted mean
  age plus counts per band (`≤23`, `24–27`, `28+`) and a coarse window hint
  (`contend` / `balanced` / `rebuild`) derived from where value concentrates.
- **Unvalued players are zero, never errors.** A player missing a `PlayerValue`
  contributes 0 and increments an `unvalued` counter so the UI can flag
  incomplete data.

## Implementation plan

1. **Create `apps/leagues/ratings.py`.** Module docstring states the
   schedule-agnostic scope and points at PR 04 for projections. Add the weight
   constants and reuse the existing `POSITION_ORDER` (import from
   `apps.leagues.views` or, to avoid a view import in a service, lift
   `POSITION_ORDER` into `apps/leagues/services.py` and import it in both — note
   this small refactor in the PR).

2. **Define the dataclasses:**
   ```python
   @dataclass(frozen=True)
   class PositionStrength:
       position: str
       value: float
       starter_value: float
       count: int

   @dataclass(frozen=True)
   class AgeProfile:
       weighted_avg_age: float | None
       young: int      # ≤23
       prime: int      # 24–27
       aging: int      # 28+
       window: str     # "contend" | "balanced" | "rebuild"

   @dataclass(frozen=True)
   class TeamRating:
       team_id: int
       overall: float
       starter_strength: float
       bench_depth: float
       taxi_depth: float
       positions: list[PositionStrength]   # league fantasy_positions order
       age: AgeProfile
       players_capital: float
       picks_capital: float                # 0.0 when 007 unavailable
       dynasty_capital: float              # players_capital + picks_capital
       unvalued: int
       is_rated: bool                      # False when no PlayerValue data
   ```

3. **Implement `player_values(players, *, season, model_version=None)`** as
   specified above — one query over `PlayerValue` filtered to the active
   `model_version`, returning `{player_pk: float}`. Unknown pks omitted (caller
   treats missing as 0.0).

4. **Implement `owned_picks_capital(team)`** — the 007 adapter. When
   `apps.leagues.models.PickValue` is importable, sum the `PickValue.value` of
   picks this team's `manager` currently owns: start from the league's base picks
   per round (each roster owns its own pick each round), apply
   `TradedPick.current_owner` / `original_owner` overrides for this
   `league_season`, and match each owned pick to a `PickValue` on
   `(league, season, round, slot=0)` (round-level, since future picks are known
   by season+round only). When `PickValue` is unavailable, return `0.0`. Keep
   this isolated so 007 integration is one function.

5. **Implement `team_rating(team)`:**
   - Pull the team's `RosterSlot`s once, `select_related("player")`, split by
     `RosterSlot.Slot` (STARTER / BENCH / TAXI / IR).
   - Call `player_values(players, season=int(team.league_season.season))` over
     all rostered players.
   - **Starter strength:** iterate `starting_lineup(team)` (from
     `apps.leagues.views`, or move that helper alongside `starting_slots` in
     `services.py` — prefer the move so `ratings.py` needn't import the views
     module; note it in the PR) so slot order and empty-slot handling match the
     roster page; sum starter values.
   - **Per-position strength:** for each position in
     `team.league_season.fantasy_positions`, total value across all non-IR slots
     and separately across starters; produce `PositionStrength` rows in that
     league's declared position order.
   - **Depth:** bench value × `BENCH_WEIGHT`, taxi value × `TAXI_WEIGHT`.
   - **Overall:** `starter_strength + bench_depth + taxi_depth`.
   - **Age profile:** value-weighted mean over `player.age` (skip null ages),
     band counts, and a `window` hint (e.g. `rebuild` when >55% of value sits in
     the `≤23` band, `contend` when >55% sits in `24–27`, else `balanced`) — pick
     thresholds and document them inline.
   - **Capital:** `players_capital` = sum of all rostered players' value
     (including IR); `picks_capital` from step 4; `dynasty_capital` their sum.
   - `is_rated = players_capital > 0`; `unvalued` = rostered players with 0 value.

6. **Keep it queryset-light.** `team_rating` should issue at most a couple of
   queries (roster slots + the value lookup). PR 03 will call it per team, so
   avoid per-player queries here.

## Testing

Add `apps/leagues/tests/test_ratings.py` (`TestCase`, no network). Build a
`LeagueSeason` with a realistic `roster_positions` (e.g.
`["QB","RB","RB","WR","WR","TE","FLEX","SUPER_FLEX","BN","BN","TAXI"]`), a
`Team`, `Player`s, and `RosterSlot`s across all four slot kinds mirroring the
`test_services.py` fixtures. Inject dynasty values by creating 006's value rows
**or**, if 006 is not yet merged, by monkeypatching `ratings.player_values` to a
fixed dict — assert the aggregation math independently of 006. Cover:

- `test_overall_is_weighted_sum` — overall equals
  `starters + bench×BENCH_WEIGHT + taxi×TAXI_WEIGHT`, IR excluded from overall.
- `test_starter_strength_uses_lineup_slots` — starter value follows
  `starting_lineup`, and an empty declared slot contributes nothing without
  shifting others.
- `test_position_strength_in_league_order` — `positions` follows
  `fantasy_positions`; a position the league doesn't start is absent.
- `test_unvalued_players_count_zero` — a rostered player with no value adds 0 and
  increments `unvalued`; `is_rated` is False when no player has value.
- `test_age_profile_bands_and_window` — band counts and the window hint for a
  young-heavy vs prime-heavy roster; null ages are skipped from the mean.
- `test_players_capital_includes_ir` — capital counts IR while overall does not.
- `test_picks_capital_zero_without_007` — with 007 unavailable,
  `picks_capital == 0.0` and `dynasty_capital == players_capital`.
- `test_query_count_bounded` — `assertNumQueries` keeps `team_rating` to the
  intended small, constant query count.

Run narrowed: `make test ARGS="apps.leagues.tests.test_ratings"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
