# 01 — Recommendation engine (on-demand rules layer)

Feature: `009_roster-insights-and-recommendations`

## Objective

Stand up the `apps/insights/` app and its recommendation engine: a pure,
deterministic function that maps a player's upstream signals — in the context of
my team — to one of `keep / sell_high / buy_low / target / cut / hold` plus a
plain-English rationale. No UI, no HTTP, no new model. This PR is the testable
core that PRs 02 and 03 surface. It ships the rule set, the data structures, and
a thin ORM-reading adapter, all covered by unit tests that use fabricated inputs
(no network, no dependence on live upstream data).

## Design decision: compute on demand, do NOT materialise

The recommendation is a **pure function of already-materialised upstream
signals** — `PlayerValue` (006), `PlayerAnalytics` (005),
`TeamPositionStrength` (008), and `TrendingPlayer`. It carries out no expensive
computation of its own; it is rule evaluation over a handful of fields. So we
compute it on demand and store nothing. Justification, to be recorded in the
`recommendations.py` module docstring:

- **No staleness coupling.** Materialising would add a cache that must be
  invalidated whenever *any* of three upstream `recompute`/sync jobs runs
  (value, analytics, or team strength). On-demand means a recommendation is
  always consistent with the signals it reads — there is no fourth table to keep
  in lockstep with the other three.
- **Bounded fan-out.** Recommendations are only ever needed for the players on a
  screen — a roster (~25–40 players) or a board page (`paginate_by = 50`), all in
  one my-team context. There is no all-players batch that would justify
  precomputation.
- **Matches the codebase precedent.** `leagues.views.free_agents` is "derived,
  never stored — being a free agent is the absence of a roster spot, so
  materialising it would just be a cache to invalidate," and the Targets overlay
  (`scouting.views._with_target_overlay`) reads per-request via correlated
  subqueries. The recommendation layer is the same shape of derived read.
- **Explainability is free.** Because the rationale is produced in the same pass
  as the label, there is nothing to persist and re-hydrate — the reasons are
  always the current reasons.

Consequently there is **no migration and no `recompute` management command** in
this feature.

## Upstream interfaces (being planned in parallel)

This engine reads three sibling features' models. They are referenced here by
their **intended** names; confirm exact field names against 005/006/008 when
implementing, and keep the read behind the adapter (below) so a rename touches
one place.

- **006 `player-dynasty-valuation` → `PlayerValue`** — per player: a dynasty
  `value` (score), a `tier` (`1` = elite), and a `position_rank`.
- **005 `player-analytics-layer` → `PlayerAnalytics`** — per player: a recent-
  form `trend` (rising / flat / falling), a `consistency` score, and an
  age-profile / age-cliff `risk` flag or curve.
- **008 `team-strength-and-outlook` → `TeamPositionStrength`** — per
  `(team, position)`: a classification of `need` / `surplus` / `balanced`.

Every read is null-tolerant: a missing row yields a neutral signal, never an
error.

## Scope

**In scope**
- `apps/insights/` app scaffold (`apps.py`, `__init__.py`, `INSTALLED_APPS`),
  label `insights`.
- `apps/insights/recommendations.py`:
  - `RecLabel` (a `TextChoices`: `KEEP`, `SELL_HIGH`, `BUY_LOW`, `TARGET`, `CUT`,
    `HOLD`) with display labels and a colour/style key per label.
  - `SignalInputs` dataclass — the neutral, model-free bundle the rules consume.
  - `Recommendation` dataclass — `label: RecLabel`, `rationale: list[str]`, plus
    the inputs it was derived from (for rendering).
  - `recommend(signals: SignalInputs) -> Recommendation` — the pure rule set.
  - `gather_recommendations(team, players) -> dict[int, Recommendation]` — the
    ORM adapter that reads the upstream models for one my-team context and calls
    `recommend` per player.
- Unit tests for the rules and the adapter.

**Out of scope**
- Any view, URL, template, or badge (PR 02).
- The roster-insights summary page and Targets integration (PR 03).
- The upstream models themselves (005/006/008).

## The rule set (transparent and explainable)

`SignalInputs` fields (all optional/neutral-defaulting):

- `on_my_roster: bool` — is the player on the my-team context's roster?
- `value_tier: int | None`, `value_score: float | None` — from `PlayerValue`.
- `form_trend: Trend` (`RISING` / `FLAT` / `FALLING` / `UNKNOWN`) — from
  `PlayerAnalytics`.
- `age_risk: bool` — past the position's age cliff, from `PlayerAnalytics`.
- `position_context: PosContext` (`NEED` / `SURPLUS` / `BALANCED` / `UNKNOWN`) —
  my team at the player's position, from `TeamPositionStrength`.
- `market_add: int`, `market_drop: int` — `TrendingPlayer` add/drop counts.

Thresholds live as named module constants (e.g. `ELITE_TIER = 1`,
`HIGH_VALUE_TIER = 2`, `LOW_VALUE_TIER`, `HOT_MARKET_ADDS`) so the rules read
declaratively and are tuneable in one place.

Evaluate in priority order; the first matching rule wins, and each rule appends
the specific reasons that fired to `rationale`:

**Players on my roster**

1. `SELL_HIGH` — high value (`value_tier <= HIGH_VALUE_TIER`) **and** at least one
   erosion signal: `form_trend == FALLING`, `age_risk`, or
   `position_context == SURPLUS`; strengthened when `market_add >= HOT_MARKET_ADDS`.
   Rationale e.g. *"Tier-2 value, form falling, and you're deep at RB — sell into
   the hype (312 adds this week)."*
2. `CUT` — low value (`value_tier` unset or `>= LOW_VALUE_TIER`) **and**
   `form_trend == FALLING` **and** `position_context != NEED`. Rationale:
   *"Low dynasty value, falling, not a position you're thin at — roster clog."*
3. `KEEP` — high value **and** `form_trend in {RISING, FLAT}` **and** not
   `age_risk`. Rationale: *"Tier-1 value, trending up, still on the right side of
   the age curve — core hold."*
4. `HOLD` — default; middling signals. Rationale names the dominant neutral
   signal.

**Players not on my roster** (free agents / rivals — the buy side)

5. `BUY_LOW` — market is dropping (`market_drop >= HOT_MARKET_ADDS` or a falling
   market) **while** underlying `form_trend in {RISING, FLAT}` **and**
   `position_context == NEED`. Rationale: *"Market's dropping him but the
   production is stable and you need WR — buy the dip."*
6. `TARGET` — solid value (`value_tier <= HIGH_VALUE_TIER`) **and**
   `position_context == NEED` **and** (`form_trend == RISING` or young / no
   `age_risk`). Rationale: *"Tier-2 WR, trending up, fills your biggest gap —
   acquire."*
7. `HOLD` — default (no actionable buy signal).

Tie-breaks and ordering are deterministic (documented inline) so the same inputs
always yield the same label — a property the tests assert.

## Implementation plan

1. **Scaffold the app.** Create `apps/insights/{__init__.py, apps.py}` mirroring
   `apps/scouting/apps.py` (`InsightsConfig`, `name = "apps.insights"`,
   `label = "insights"`). Add `"apps.insights"` to `INSTALLED_APPS` in
   `config/settings.py` (after `"apps.scouting"`). No `models.py` content beyond
   an empty module (the app has no models); confirm `make migrate` needs nothing
   for it.

2. **`apps/insights/recommendations.py`.** Define, in order:
   - Module docstring capturing the on-demand decision (from the section above).
   - `RecLabel(models.TextChoices)` and small `Trend` / `PosContext` enums (plain
     `enum.Enum` or `TextChoices`), plus a `LABEL_STYLE: dict[RecLabel, str]`
     mapping each label to a Tailwind colour key (green keep/target, amber
     sell_high, sky buy_low, red cut, slate hold) that PR 02's template reads.
   - Named threshold constants.
   - `@dataclass(frozen=True) SignalInputs` and
     `@dataclass(frozen=True) Recommendation`.
   - `recommend(signals) -> Recommendation` implementing the priority ladder,
     building `rationale` as it goes.

3. **ORM adapter `gather_recommendations(team, players)`.** For a `Team` (the
   my-team context) and an iterable/queryset of `Player`:
   - Resolve which of `players` are on `team`'s roster (one
     `RosterSlot.objects.filter(team=team).values_list("player_id", flat=True)`
     set).
   - Bulk-read the upstream signals with correlated subqueries / `in_bulk`
     lookups keyed by `player_id`, importing the upstream models by their
     intended names inside the function (so an import error surfaces clearly if
     run before 005/006/008 land). Wrap each upstream read so a `None`/missing row
     maps to the neutral signal value.
   - Read `TrendingPlayer` add/drop counts (this model exists today) the same way
     `leagues.views.free_agents` does.
   - Build a `SignalInputs` per player and return
     `{player_id: recommend(inputs)}`.
   - Keep this the **only** place that touches the upstream ORM, so PRs 02/03 and
     the tests depend solely on `SignalInputs` / `Recommendation`.

4. **No admin, no migration, no command** — assert this explicitly in the PR so a
   reviewer knows the absence is deliberate (the on-demand decision).

## Testing

Add `apps/insights/tests/{__init__.py, test_recommendations.py}`. All inputs are
fabricated `SignalInputs` — **no** upstream rows required, so the rule tests run
green before 005/006/008 exist.

Rule engine (`recommend`), one test per branch and rationale:
- `test_sell_high_on_falling_high_value` — high tier + falling form ⇒ `SELL_HIGH`;
  rationale mentions falling form.
- `test_sell_high_on_positional_surplus` — high tier + `SURPLUS` ⇒ `SELL_HIGH`.
- `test_cut_low_value_falling_not_needed` ⇒ `CUT`.
- `test_keep_core_asset` — high tier, rising, no age risk ⇒ `KEEP`.
- `test_hold_default_on_roster` — middling ⇒ `HOLD`.
- `test_buy_low_market_dropping_need` — off-roster, dropping market, stable form,
  `NEED` ⇒ `BUY_LOW`.
- `test_target_fills_gap` — off-roster, solid value, `NEED`, rising ⇒ `TARGET`.
- `test_no_buy_signal_holds` — off-roster, `BALANCED` ⇒ `HOLD`.
- `test_missing_signals_degrade_to_hold` — an all-`None`/`UNKNOWN` `SignalInputs`
  never raises and returns `HOLD`.
- `test_deterministic` — the same inputs yield an equal `Recommendation` (label +
  rationale) across calls.
- `test_rationale_nonempty_for_actionable_labels` — every non-`HOLD` label has at
  least one rationale line.

Adapter (`gather_recommendations`) — a `TestCase` that creates a `League` /
`LeagueSeason` / `Team` / `Manager(is_me=True)` / `Player` / `RosterSlot` and, if
the upstream models are importable at implementation time, a `PlayerValue` /
`PlayerAnalytics` / `TeamPositionStrength` row; assert the returned dict is keyed
by `player_id` and that a rostered player with fabricated signals gets the
expected label. If an upstream model is not yet available, gate that assertion
with a skip and keep the on-roster / trending-only path covered (that path uses
only models that exist today). No test performs network I/O.

Run narrowed: `make test ARGS="apps.insights"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
