# 02 — Baseline pick-value compute, command & make target

Feature: `007_draft-pick-valuation`

## Objective

Compute pick values. A `recompute_pick_values` service builds a **baseline
round-level value curve** from historical rookie outcomes, materialises
`PickValue` rows for the picks each league has, and exposes a
`pick_value_for(...)` read helper for PR 03 and feature `010`. A
`recompute_pick_values` management command and a `make recompute-pick-values`
target drive it, wrapped in a `SyncRun`. No view work here.

## Dependency on feature 006 (`player-dynasty-valuation`)

This PR **reads** `PlayerValue` — the per-player dynasty value produced by
feature `006`, being planned in parallel. It is written against `006`'s intended
interface, not a shipped model, so:

- Reference `PlayerValue` **by name** and assume the minimal shape this compute
  needs: one current dynasty value per `Player`, reachable as
  `PlayerValue.objects` with a FK `player` and a numeric value field (assume
  `value`; adapt to `006`'s final field name at implementation time, in one
  place — the `_player_values()` accessor below).
- The compute must **degrade gracefully** when there are zero `PlayerValue` rows
  (e.g. `006` not yet run): write nothing, record `records_written=0`, and do not
  raise — so this feature is implementable and testable ahead of a full `006`.
- **Ordering across features:** PR 02 cannot be *verified end-to-end* until
  `006`'s `PlayerValue` lands. Its tests seed a stand-in `PlayerValue` (or, if
  `006` has merged by then, the real one). Flag this at the review checkpoint.

## Baseline model (justify in the service docstring)

Chosen v1: a **round-level value curve from historical rookie cohorts**, using
only data we actually have. Sleeper exposes no draft-slot → player mapping (the
rookie-draft endpoints are a separate, unbuilt backlog item), so we cannot look
up "who was picked 1.05 in 2022". What we *can* do:

1. **Rookie classes.** Group `Player` rows by `rookie_year` (a real column, see
   `apps/players/models.py`). Each class is one draft's worth of players.
2. **Realised value.** Join each rookie to its current `PlayerValue`. A player's
   *realised* dynasty value now is the outcome that a pick spent on them bought.
3. **Rank as a proxy for draft position.** Within a class, rank players by
   realised `PlayerValue` descending. The Nth-best rookie approximates the Nth
   pick — a defensible stand-in for the unknown true draft order.
4. **Bucket into rounds.** With `picks_per_round` picks per round (the league's
   team count — `LeagueSeason.total_rosters` of the current season, default `12`),
   rank `r` falls in `round = ceil(r / picks_per_round)`. The per-round value for
   a class is the mean realised value of its bucket.
5. **Average across classes.** Average each round's per-class means over the last
   `K` complete rookie classes (default `5`) → the curve `round -> value`.
   `sample_size` on each written row is the number of classes that fed it.

This is honestly bounded by the data: it is a *round* curve, not a slot curve,
because slot truth is unavailable. Slot refinement (below) is an optional,
clearly-approximate extra, not the headline. Tuning constants (`K`,
`picks_per_round` fallback, the future-season discount) live as module constants
so they are easy to revisit when `006` and real draft data mature.

## Scope

**In scope**
- `apps/leagues/valuation.py` — new module: the compute service, the round-curve
  math, the `pick_value_for` read helper, and tuning constants
- `apps/leagues/management/commands/recompute_pick_values.py`
- `make recompute-pick-values` target + `.PHONY` entry in the `Makefile`
- A new `SyncRun.Kind` for the compute
- Tests seeding `Player` + `PlayerValue` + `TradedPick` fixtures (no network)

**Out of scope**
- The `PickValue` model itself (PR 01)
- Surfacing values on the trades view (PR 03)
- Producing `PlayerValue` — that is feature `006`; here it is read-only input
- True per-slot draft-order data (needs the unbuilt rookie-draft ingest)

## Implementation plan

1. **`SyncRun.Kind`.** Add `PICK_VALUES = "pick_values", "Pick values"` to
   `SyncRun.Kind` in `apps/sleeper/models.py` and migrate
   (`make makemigrations ARGS="sleeper --name pick_values_kind"`). Note for the
   parallel `006`: if it adds a generic `valuation` kind, reconcile to avoid two
   overlapping kinds — this feature's compute is distinct enough to own
   `pick_values`.

2. **Module + constants** in `apps/leagues/valuation.py`:

   ```python
   DEFAULT_PICKS_PER_ROUND = 12   # fallback when a league's team count is unknown
   DEFAULT_ROOKIE_CLASSES = 5     # K: how many recent classes feed the curve
   DEFAULT_ROUNDS = 5             # rounds to value when a pick's round is missing
   FUTURE_SEASON_DISCOUNT = 1.0   # per-year-out discount hook; 1.0 = no discount v1
   ```

3. **`_player_values()` accessor** — the *single* place that touches `006`'s
   model, returning `{player_id: value}` for all players that have a
   `PlayerValue`. Isolating the import/field name here means adapting to `006`'s
   final shape is a one-line change and the graceful-empty path is trivial
   (`{}` when the table is empty or the model import fails).

4. **`round_value_curve(...)`** — pure function, no DB writes, unit-testable:
   given the `{player_id: value}` map, `Player` rookie metadata, `picks_per_round`
   and `K`, produce `{round: (mean_value, class_count)}` per the baseline model
   above. Take only complete classes (exclude the current incoming class, which
   has no realised value yet — an incoming rookie's `rookie_year == current
   season` should be skipped; resolve the current season from the league's
   newest `LeagueSeason.season`). Returns an empty dict when there are no valued
   rookies, which drives the graceful-degrade path.

5. **`recompute_pick_values(...)` service** — the orchestrator:

   ```python
   def recompute_pick_values(
       *,
       league: League | None = None,
       seasons: Sequence[str] | None = None,
       with_slots: bool = False,
       dry_run: bool = False,
   ) -> SyncStats:
   ```
   - Loop the target leagues (`[league]` or `League.objects.all()`).
   - For each: derive `picks_per_round` from the current `LeagueSeason`'s
     `total_rosters` (fallback `DEFAULT_PICKS_PER_ROUND`), build the curve, then
     decide which `(season, round)` pairs to materialise:
     - every distinct `(season, round)` present in the league's `TradedPick`
       rows (so the trades view has a value for every pick it shows), **plus**
     - rounds `1..DEFAULT_ROUNDS` for the next few upcoming pick seasons observed
       in `TradedPick` (covers untraded rounds a future consumer may ask for).
   - Write a `slot=0` `PickValue` per pair with
     `value = curve[round].mean * discount(season)`, `method="baseline_round_curve"`,
     `sample_size=curve[round].class_count`, `computed_at=timezone.now()`.
   - **Idempotent upsert** mirroring `upsert_players` in
     `apps/players/services.py`: `PickValue.objects.bulk_create(rows,
     update_conflicts=True, unique_fields=["league", "season", "round", "slot"],
     update_fields=["value", "method", "sample_size", "computed_at", "updated_at"])`,
     setting `updated_at = timezone.now()` explicitly on each instance (bulk
     upsert bypasses `TimeStampedModel.auto_now`, the exact caveat `sync_stats`
     documents).
   - **Slot refinement** (only when `with_slots`): also compute per-slot means
     (rank `r`'s slot within its round is `((r - 1) % picks_per_round) + 1`) and
     write `slot >= 1` rows alongside the `slot=0` fallback. Keep it plainly
     labelled `method="baseline_slot_curve"` and behind the flag.
   - Wrap the whole run in `with SyncRun.track(SyncRun.Kind.PICK_VALUES) as run:`,
     accumulate into `SyncStats` (reuse the dataclass from
     `apps/players/services.py` or add a local one), set
     `run.records_written` / `run.records_skipped`. Empty curve → zero written,
     success (not an error). `dry_run` computes but skips the upsert.

6. **`pick_value_for(league, season, round, slot=0)` read helper** in the same
   module — the interface PR 03 and feature `010` consume. Resolve most-specific
   first: exact `(league, season, round, slot)`, then the round-level `slot=0`
   row, returning the `PickValue` (or `None`). A convenience
   `pick_values_for_league(league) -> dict[(season, round, slot), PickValue]`
   lets the view fetch all a league's values in one query.

7. **Command** `apps/leagues/management/commands/recompute_pick_values.py`,
   modelled on `sync_stats.py`:
   - `--league <slug>` (default: all leagues), `--season` (repeatable/comma list,
     restrict which pick seasons are materialised), `--with-slots`, `--dry-run`.
   - Resolve `--league` via `League.objects.get(slug=...)`, translating a miss
     into `CommandError`. Call the service; print a
     `self.style.SUCCESS(f"Wrote {stats.written} pick value(s); skipped {stats.skipped}.")`
     line. On zero valued rookies, print a clear note that `PlayerValue` data
     (feature `006`) is missing rather than failing.

8. **Makefile** — add next to `sync-stats`, and to `.PHONY`:
   ```make
   recompute-pick-values:  ## Recompute draft-pick values from historical rookie outcomes
   	$(EXEC) python manage.py recompute_pick_values $(ARGS)
   ```

## Testing

Add `apps/leagues/tests/test_pick_valuation.py` (`TestCase`) and a command case
in the leagues command tests. Seed `Player` rows across a few `rookie_year`
classes, a stand-in `PlayerValue` per player (the isolated `_player_values()`
accessor makes this a small fixture), a `League`/`LeagueSeason` with a known
`total_rosters`, and a handful of `TradedPick` rows. **No test hits the network.**

- `test_round_curve_orders_rounds` — with a hand-built value map, round 1 > round
  2 > round 3 means (higher-valued rookies bucket into earlier rounds).
- `test_curve_excludes_incoming_class` — a rookie whose `rookie_year` is the
  current season contributes nothing (no realised value yet).
- `test_recompute_writes_round_level_rows` — a `slot=0` `PickValue` is written for
  each `(season, round)` in the league's `TradedPick` set, with `method`,
  `sample_size`, and `computed_at` populated.
- `test_recompute_is_idempotent` — running twice produces no duplicate rows and
  updates `value`/`updated_at` in place (mutate a `PlayerValue` between runs and
  assert the pick value moved).
- `test_graceful_when_no_player_values` — with an empty `PlayerValue` table the
  run completes, writes zero, and the `SyncRun` is `success` (the `006`-not-ready
  guard).
- `test_with_slots_writes_slot_rows` — `with_slots=True` adds `slot >= 1` rows
  and keeps the `slot=0` fallback.
- `test_records_failure` — force an error inside the loop; `SyncRun.status ==
  "failed"` and the error is captured.
- `test_pick_value_for_falls_back_to_round_level` — `pick_value_for` returns the
  exact slot row when present, else the `slot=0` row, else `None`.
- `test_dry_run_writes_nothing`.
- Command: `test_recompute_command` (via `call_command`, asserts the success line
  and rows written), `test_recompute_command_league_flag` (unknown slug →
  `CommandError`), `test_recompute_command_with_slots`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started. Note explicitly at handoff whether
`006`'s `PlayerValue` has landed yet, and whether the `_player_values()` accessor
was pointed at the real model or a stand-in.
