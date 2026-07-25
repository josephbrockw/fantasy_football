# 01 — PickValue model, migration & admin

Feature: `007_draft-pick-valuation`

## Objective

Add the `PickValue` model — the schema that holds one estimated dynasty value
per draft pick — with its migration and admin registration. Pure data layer: no
compute, no view. It gives PR 02 a table to write into and PR 03 a row to read.

## Scope

**In scope**
- `PickValue` model in `apps/leagues/models.py` (next to `TradedPick`)
- The migration for it
- Admin registration in `apps/leagues/admin.py`
- Model-level tests (fields, key, `__str__`, the `slot=0` convention)

**Out of scope**
- The compute service / `recompute_pick_values` command (PR 02)
- The `pick_value_for` read helper (PR 02, next to the compute it reads back)
- Any change to `TradesView` or `trades.html` (PR 03)
- Importing or depending on `PlayerValue` — this PR touches no valuation code

## Design decisions (justify in the model docstring)

- **Home: the `leagues` app.** A draft pick is a leagues-domain concept —
  `TradedPick`, `Trade`, `TradeAsset` all live in `apps/leagues/models.py`, and
  `PickValue` is read straight back by `TradesView`. It *consumes* `PlayerValue`
  (feature `006`) at compute time but is not part of the player-valuation schema,
  so it belongs beside `TradedPick`, not in `006`'s app. (If `006` establishes a
  dedicated valuation app it could later move; leagues is the grounded home now.)
- **Scoped to `League`, not `LeagueSeason`.** A pick's `season` is frequently a
  **future** year with no `LeagueSeason` row yet (the same reason `TradedPick`
  keys owners on `Manager`, per that model's docstring). `League` is the
  permanent record and carries the league's pick-per-round count and its players'
  valuations, so `PickValue` hangs off `League` and stores the pick's own
  `season` as a plain `CharField` matching `TradedPick.season`.
- **`slot=0` sentinel for round-level.** `/traded_picks` gives only season +
  round for future picks — the exact within-round slot is usually unknown. Rather
  than a nullable `slot` (whose `NULL`s would each be distinct under a Postgres
  unique index and silently permit duplicate round-level rows), use a
  `PositiveIntegerField` where `0` means "round-level, slot unknown" and `>= 1`
  is a specific pick. `unique_together` then behaves.
- **`value` is a plain `FloatField` on the `PlayerValue` scale.** So a pick and a
  player are directly comparable — the whole point. Provenance (`method`,
  `sample_size`, `computed_at`) travels with the row so a value can be trusted
  and re-explained without re-running the compute.

## Implementation plan

1. **Model** in `apps/leagues/models.py`, after `TradedPick`:

   ```python
   class PickValue(TimeStampedModel):
       """Estimated dynasty value of a single draft pick.

       The pick side of the ledger, on the same scale as ``PlayerValue`` so a
       pick and a player are directly comparable. Keyed on ``League`` (not
       ``LeagueSeason``) because a pick's ``season`` is usually a future year
       with no season row yet — the same reason ``TradedPick`` keys on
       ``Manager``. ``slot`` is the within-round pick number; ``0`` is the
       sentinel for "round-level, exact slot unknown", which is the normal case
       for a future pick that ``/traded_picks`` reports by season + round only.
       Written by ``recompute_pick_values`` (PR 02); read by ``TradesView``.
       """

       league = models.ForeignKey(
           League, on_delete=models.CASCADE, related_name="pick_values"
       )
       # The pick's own season, e.g. "2027" — matches ``TradedPick.season``.
       season = models.CharField(max_length=8)
       round = models.PositiveIntegerField()
       # Within-round pick number; 0 = round-level (slot unknown).
       slot = models.PositiveIntegerField(default=0)

       # Expected dynasty value, on the same scale as ``PlayerValue``.
       value = models.FloatField()

       # Provenance, so a value can be trusted and re-explained.
       method = models.CharField(max_length=32, blank=True)
       sample_size = models.PositiveIntegerField(default=0)
       computed_at = models.DateTimeField(null=True, blank=True)

       class Meta:
           unique_together = ("league", "season", "round", "slot")
           ordering = ["season", "round", "slot"]
           indexes = [
               models.Index(fields=["league", "season"]),
               models.Index(fields=["league", "season", "round"]),
           ]

       def __str__(self) -> str:
           label = f"{self.season} R{self.round}"
           if self.slot:
               label += f".{self.slot}"
           return f"{label} pick ≈ {self.value:.1f}"
   ```

2. **Migration.** `make makemigrations ARGS="leagues --name pickvalue"`, then
   `make migrate`. Confirm it is a single additive `CreateModel` touching nothing
   else.

3. **Admin** in `apps/leagues/admin.py`: register `PickValue` following the
   existing registrations in that file (a `@admin.register(PickValue)` with a
   `list_display` of `league`, `season`, `round`, `slot`, `value`, `method`,
   `computed_at`; `list_filter` on `league`, `season`, `round`; ordering by
   `-season`). Match whatever style the other model admins in the file already
   use (plain `ModelAdmin` vs. `TimeStampedModel` conventions).

## Testing

Add `apps/leagues/tests/test_pick_value_model.py` (`TestCase`), seeding a
`League` via the existing league factories/helpers in `apps/leagues/tests/`:

- `test_create_and_str_round_level` — a `slot=0` row stringifies as
  `"2027 R1 pick ≈ …"` (no slot suffix).
- `test_str_includes_slot_when_specific` — a `slot=3` row includes `".3"`.
- `test_unique_per_league_season_round_slot` — a second row with the same
  `(league, season, round, slot)` raises `IntegrityError`; the same key differing
  only by `slot` is allowed (proves the sentinel keeps round-level and per-slot
  rows distinct).
- `test_ordering` — rows come back ordered by `season, round, slot`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
