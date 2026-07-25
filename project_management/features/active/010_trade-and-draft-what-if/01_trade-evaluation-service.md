# 01 — Trade evaluation service

Feature: `010_trade-and-draft-what-if`

## Objective

A pure, no-DB-write Python service that, given a hypothetical trade between two
`Team`s (players + picks each way), returns each side's total value, the net
value delta with a deterministic verdict, and each involved team's rating /
outlook / positional breakdown **before vs after**. This is the computational
core the UI (PR 02) and the draft what-if (PR 03) both call. No view, no
template, no migration — this PR is the function and its tests.

## Scope

**In scope**
- A new module `apps/analytics/whatif.py` (match the real analytics app name from
  006–008) with the dataclasses and `evaluate_trade(proposal)` entrypoint.
- Composing `PlayerValue` (006), `PickValue` (007), and 008's pure roster-rating
  entrypoint into a single `TradeEvaluation`.
- The win / fair / loss verdict band as named constants.
- Ensuring 008 exposes a **pure `rate_roster(players, league_season)`-style**
  entrypoint (add it in 008's module if 008 only rates persisted `Team`s — this
  is the one cross-feature touch this PR may need).

**Out of scope**
- Any HTTP view, URL, template, or nav change (PR 02).
- The one-sided draft/add path and rookie selection (PR 03).
- FAAB as a tradable asset (v1 values players + picks only).
- Persisting anything: no `Trade` / `TradeAsset` / `TradedPick` writes, no new
  model, no migration. This is derived, ephemeral computation.

## Implementation plan

The service mirrors how `apps/leagues/views.py` prepares data with plain
dataclasses (`LineupRow`, `TradeSummary`) and module-level pure functions
(`starting_lineup`, `trade_summaries`) — logic in Python, no side effects.

1. **Asset refs and proposal** in `apps/analytics/whatif.py`. Mirror the shape of
   `TradeAsset` (`apps/leagues/models.py`) without touching the DB:
   ```python
   @dataclass(frozen=True)
   class PlayerRef:
       player_id: int

   @dataclass(frozen=True)
   class PickRef:
       season: str   # e.g. "2027" — often a future year, like TradedPick.season
       round: int

   Asset = PlayerRef | PickRef

   @dataclass(frozen=True)
   class TradeProposal:
       team_a: Team                 # "my" team by convention (see below)
       team_b: Team
       a_sends: list[Asset]         # assets leaving team_a → team_b
       b_sends: list[Asset]         # assets leaving team_b → team_a
   ```
   `team_a` is the perspective team (the deltas are framed from its side); the UI
   passes my team as `team_a`.

2. **Value lookup helpers.** Resolve values from the upstream models, tolerating a
   missing valuation (a player/pick 006/007 hasn't scored yet contributes `0` and
   is flagged, never raising):
   - `_player_values(player_ids) -> dict[int, Decimal]` — one `PlayerValue`
     query (`filter(player_id__in=…)`), not per row.
   - `_pick_values(refs, league_season) -> dict[tuple[str, int], Decimal]` — one
     `PickValue` query keyed on `(season, round)` (match 007's key; picks are
     league/format-scoped like `PickValue` defines).
   Use `Decimal` end-to-end to match `Team.points_for`'s `DecimalField`
   convention and avoid float drift.

3. **Side valuation.**
   ```python
   @dataclass
   class SideValuation:
       assets: list[tuple[Asset, str, Decimal]]  # (ref, label, value)
       total: Decimal
       unvalued: list[str]                        # labels 006/007 couldn't price
   ```
   Build one `SideValuation` for `a_sends` and one for `b_sends`. Reuse a
   `label(asset)` helper that reads like `TradeAsset.label` ("Josh Allen",
   "2027 R1 pick").

4. **Hypothetical rosters + rating.** Team rating before/after comes from 008's
   pure entrypoint, **not** from any stored rating:
   - `_current_players(team) -> list[Player]` — the team's rostered players via
     `RosterSlot` (`RosterSlot.objects.filter(team=team).select_related("player")`).
   - `_apply(players, remove_ids, add_players)` — the post-trade player set:
     drop the `PlayerRef`s this team sends, add the `PlayerRef`s it receives.
     Picks are **not** roster members, so they never enter this set (documented
     inline — they land in the value ledger only).
   - Call 008's `rate_roster(players, league_season)` twice per team (before and
     after) to get a `TeamRating` (rating scalar + outlook + per-position
     breakdown). If 008 rates by `Team`, add the pure overload here.
   ```python
   @dataclass
   class TeamDelta:
       team: Team
       before: TeamRating          # 008's rating value object
       after: TeamRating
       # convenience deltas the template renders without arithmetic:
       rating_change: Decimal
       positional_changes: list[PositionDelta]   # (position, before, after)
   ```

5. **Verdict band.** The *only* judgement in an otherwise deterministic pipeline —
   defined as named constants so it's tunable and testable:
   ```python
   # Net value (team_a's incoming − outgoing) as a fraction of the larger side.
   FAIR_BAND = Decimal("0.10")   # within ±10% of even → "fair"
   ```
   `verdict(net, gross) -> "win" | "fair" | "loss"` from `team_a`'s perspective.
   Document that this threshold is the sole tunable judgement; everything else is
   a deterministic function of the upstream valuations/ratings.

6. **Assemble `evaluate_trade`.**
   ```python
   @dataclass
   class TradeEvaluation:
       proposal: TradeProposal
       side_a: SideValuation        # what team_a sends
       side_b: SideValuation        # what team_b sends (i.e. team_a receives)
       net_value: Decimal           # side_b.total − side_a.total (team_a's gain)
       verdict: str
       team_a_delta: TeamDelta
       team_b_delta: TeamDelta
   ```
   `evaluate_trade(proposal) -> TradeEvaluation` wires steps 2–5 together and does
   **no** writes. Keep it a module-level function (like `trade_summaries`), not a
   class, so PR 02/03 import it directly.

7. **Draft/add seam (for PR 03).** Design `evaluate_trade` so a one-sided add is a
   degenerate proposal (`b_sends` = the added assets, `a_sends = []`, and
   `team_b` optionally `None` for a free-agent/rookie add that comes from nobody).
   Leave the actual one-sided helper to PR 03, but don't hard-require `team_b`
   where it isn't needed — guard the `team_b_delta` computation on `team_b`.

## Testing

Add `apps/analytics/tests/test_whatif.py` (`TestCase`, no network, no HTTP). Seed
with the existing league/team factories plus fixture `PlayerValue` / `PickValue`
rows and a stub/real 008 rating; if 008's rater is expensive or unstable, patch
it to a simple deterministic function so these tests isolate the *composition*.

- `test_side_totals` — each side's `total` is the sum of its assets' values;
  players and picks both counted.
- `test_net_value_and_perspective` — `net_value` is team_a's incoming minus
  outgoing; sign flips when the deal is reversed.
- `test_unvalued_asset_is_zero_and_flagged` — a player with no `PlayerValue`
  contributes 0 and appears in `unvalued`, no exception.
- `test_verdict_bands` — parametrized around `FAIR_BAND`: clearly-lopsided →
  win/loss, near-even → fair.
- `test_rating_before_after` — post-trade roster has sent players removed and
  received players added; `rate_roster` is called with those exact sets;
  `rating_change` = after − before.
- `test_picks_excluded_from_roster_rating` — a pick in the deal moves `net_value`
  but does **not** change either team's rated player set.
- `test_positional_changes` — trading a WR for a RB shifts the per-position
  breakdown accordingly.
- `test_no_db_writes` — wrap `evaluate_trade` in `assertNumQueries` for reads only
  and assert `Trade.objects.count()` / `TradeAsset` / `TradedPick` are unchanged
  (0) afterward — the read-only guarantee.
- `test_one_sided_proposal` — `a_sends=[]`, `team_b=None`: returns a valuation and
  a `team_a_delta` without erroring (the seam PR 03 builds on).

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or PR 02 is started.
