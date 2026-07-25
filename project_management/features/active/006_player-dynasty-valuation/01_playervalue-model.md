# 01 — PlayerValue model & migration

Feature: `006_player-dynasty-valuation`

## Objective

Add the `PlayerValue` model that stores one dynasty valuation per
`(player, season, model_version)` — the **three sub-scores** (`now_score`,
`prospect_score`, `horizon_score`, plus the `horizon_seasons` /
`expires_season` expiration pair), the blended `value`, tier, ranks, a snapshot
position, and an inspectable `components` breakdown — plus its migration and
admin registration. This PR is schema only: no compute, no views. It establishes
the interface that PR 02 writes to and PR 03 reads from, and the `model_version`
discriminator that lets a future trained model reuse the table. Storing the
sub-scores as **columns** (not just inside `components`) is what lets PR 03
re-blend them under any weight profile at query time with `F()` arithmetic.

## Scope

**In scope**
- `apps/players/models.py` — the `PlayerValue` model (natural key, indexes,
  `components` JSON, `model_version`)
- The generated migration under `apps/players/migrations/`
- `apps/players/admin.py` — a `PlayerValueAdmin`
- A model-level test module asserting the constraints and defaults

**Out of scope**
- The valuation algorithm, `recompute_values` command, and `SyncRun.Kind`
  addition (PR 02)
- Any template / view / sort change (PR 03)
- Reading from 005's `PlayerSeasonMetrics` — nothing in this PR imports it

## Design notes

- **One row per `(player, season, model_version)`.** Season-scoped because a
  dynasty value is an as-of-season snapshot (a 27-year-old RB is worth less next
  year); `model_version` on the key so `baseline-v1` and a later `trained-v1` can
  both store a row for the same player/season and be compared, rather than one
  clobbering the other. This mirrors `PlayerWeekStat`'s "one table, discriminated
  by `kind`" decision from 004.
- **Snapshot `position` on the row.** Copied from `Player` at compute time so
  positional ranking, tiering, and scarcity are stable and the value overlay in
  PR 03 can rank within position without re-joining `Player`. (A player's
  position effectively never changes, but storing it keeps the valuation
  self-contained and the indexes useful.)
- **Sub-scores are real columns.** `now_score`, `prospect_score`, and
  `horizon_score` (each 0–100) earn columns — not just `components` keys —
  because PR 03 re-blends them per weight profile in querysets
  (`w_now * F("now_score") + …`) and JSON keys can't do that portably.
  `horizon_seasons` (float) and `expires_season` (the displayable "holds form
  through" year) ride along. All are non-null with sensible defaults written by
  the compute; `expires_season` is nullable (unknown age → positional default
  seasons but no hard year is still representable).
- **`components` JSONField** holds every intermediate the baseline produced, so a
  surprising number is debuggable from the admin without re-running the compute —
  and so a trained model can store feature attributions in the same slot. Follows
  the `raw` / `stats` JSONField convention already in `Player` / `PlayerWeekStat`
  (a new component key never costs a migration).
- **`value` is the stored blend (0–100) under the default `balanced` profile;
  `raw` intermediates live in `components`.** The column is what admin and
  default sorts use; profile-specific blends are computed at read time from the
  sub-score columns, so they never need storage.
- Extends `TimeStampedModel` (like `PlayerWeekStat`), so `updated_at` doubles as
  "computed_at" and the dashboard freshness card (PR 02's `SyncRun`) has a
  timestamp to read.

## Implementation plan

1. **Model** in `apps/players/models.py`, after `PlayerWeekStat`:

   ```python
   class PlayerValue(TimeStampedModel):
       """A three-axis dynasty valuation for one player, season, and model.

       Written by ``manage.py recompute_values`` (PR 02). ``now_score`` is
       this-season lineup value, ``prospect_score`` is breakout likelihood,
       and ``horizon_score`` / ``horizon_seasons`` / ``expires_season`` are the
       expiration axis (how long current form holds). ``value`` is the stored
       blend of the three under the default ``balanced`` weight profile; other
       profiles re-blend the sub-score columns at query time. ``components``
       keeps every intermediate so the numbers are inspectable and a trained
       model can store feature attributions in the same field. ``model_version``
       is on the natural key so ``baseline-v1`` and a future ``trained-v1``
       coexist for comparison — the app reads whichever
       ``ACTIVE_MODEL_VERSION`` names.
       """

       player = models.ForeignKey(
           Player, on_delete=models.CASCADE, related_name="values"
       )
       season = models.PositiveSmallIntegerField()
       model_version = models.CharField(max_length=32, default="baseline-v1")

       # Snapshot of the player's position at compute time, so positional rank /
       # tier / scarcity are stable and the overlay never re-joins Player.
       position = models.CharField(max_length=8, blank=True)

       # The three axes, each normalized 0–100 across the scored pool. Columns
       # (not JSON keys) so weight profiles can re-blend them in querysets.
       now_score = models.FloatField(default=0.0)
       prospect_score = models.FloatField(default=0.0)
       horizon_score = models.FloatField(default=0.0)

       # Expiration: expected seasons of remaining form (position age curve),
       # and the human-readable "holds form through" season.
       horizon_seasons = models.FloatField(default=0.0)
       expires_season = models.PositiveSmallIntegerField(null=True, blank=True)

       # Blended dynasty value, 0–100, under WEIGHT_PROFILES["balanced"]. The
       # pre-normalization ``raw`` numbers and all factors live in ``components``.
       value = models.FloatField()
       tier = models.PositiveSmallIntegerField(null=True, blank=True)
       position_rank = models.PositiveSmallIntegerField(null=True, blank=True)
       overall_rank = models.PositiveSmallIntegerField(null=True, blank=True)

       # Every intermediate (production, recent-form blend, age multiplier,
       # scarcity weight, market nudge, raw, normalized) — so a value is
       # debuggable and a trained model can drop attributions here.
       components = models.JSONField(default=dict, blank=True)

       class Meta:
           unique_together = ("player", "season", "model_version")
           ordering = ["-season", "model_version", "-value"]
           indexes = [
               # The overlay in PR 03: active model, one season, by value desc.
               models.Index(fields=["season", "model_version", "-value"]),
               # Positional ranking / tiering within a season.
               models.Index(fields=["season", "model_version", "position", "-value"]),
               # Per-player lookup across seasons (value history / admin).
               models.Index(fields=["player", "model_version"]),
           ]

       def __str__(self) -> str:
           return f"{self.player} {self.season} {self.model_version} v={self.value:.1f}"
   ```

2. **Migration** — `make makemigrations ARGS="players --name playervalue"`, then
   `make migrate`. Confirm it is a single `CreateModel` with the
   `unique_together` and the three indexes; do not hand-edit.

3. **Admin** in `apps/players/admin.py`, mirroring `PlayerWeekStatAdmin`
   (the `Player` table is large, so `raw_id_fields` on the FK):

   ```python
   @admin.register(PlayerValue)
   class PlayerValueAdmin(admin.ModelAdmin):
       list_display = (
           "player", "season", "model_version", "position",
           "value", "now_score", "prospect_score", "horizon_score",
           "expires_season", "tier", "position_rank", "overall_rank",
       )
       list_filter = ("model_version", "season", "position")
       search_fields = ("player__full_name",)
       raw_id_fields = ("player",)
       readonly_fields = ("components", "created", "updated")
       ordering = ("-season", "-value")
   ```
   (Match the actual `TimeStampedModel` timestamp field names — use whatever
   `PlayerWeekStatAdmin`/the base model expose; adjust `readonly_fields`
   accordingly.)

4. **Import** — add `PlayerValue` to the `from apps.players.models import ...`
   line in `apps/players/admin.py`.

## Testing

Add `apps/players/tests/test_value_model.py` (Django `TestCase`, no network):

- `test_natural_key_is_unique` — creating two `PlayerValue` rows with the same
  `(player, season, model_version)` raises `IntegrityError`; the same player with
  a different `model_version` **or** a different `season` is allowed (proves both
  the discriminator and the season scoping).
- `test_defaults` — a row created with only the required fields defaults
  `model_version` to `"baseline-v1"`, `components` to `{}`, the three sub-scores
  and `horizon_seasons` to `0.0`; `tier` / ranks / `expires_season` are
  nullable.
- `test_sub_scores_roundtrip` — `now_score` / `prospect_score` /
  `horizon_score` / `horizon_seasons` / `expires_season` store and read back
  as set (guards the columns PR 03's profile re-blend depends on).
- `test_components_roundtrips` — a dict stored in `components` reads back intact
  (JSONField smoke test / regression guard for the inspection contract).
- `test_str` — `__str__` includes the player, season, model version and value.
- `test_ordering` — default `Meta.ordering` returns newest season, then highest
  value first (guards the overlay's assumption).

Use a small `make_player(...)` helper (or the existing players test factory) for
setup. No `PlayerSeasonMetrics` or compute is involved here.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
