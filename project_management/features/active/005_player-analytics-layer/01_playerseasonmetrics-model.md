# 01 — PlayerSeasonMetrics model & migration

Feature: `005_player-analytics-layer`

## Objective

Add the storage layer for the derived per-player-per-season metrics — one
`PlayerSeasonMetrics` model — plus its migration and admin registration. No
recompute service, no command: a reviewable data-model foundation that PR 02
fills. This is the materialized feature store that 006's valuation reads.

## Scope

**In scope**
- `PlayerSeasonMetrics` model in `apps/players/models.py`
- Migration for `apps/players`
- Admin registration in `apps/players/admin.py`
- Model-level tests

**Out of scope**
- The recompute service and `recompute_metrics` command + `make` target (PR 02)
- The `SyncRun.Kind.METRICS` audit kind (added in PR 02, where the recompute
  uses it)
- The read-only report command (PR 03)
- Any player-facing web view

## Design decision: materialize per (player, season, season_type)

The grain matches `PlayerWeekStat`'s natural aggregation: fantasy value is judged
season by season, so one row per `(player, season, season_type)` is the unit 006
will join against `Player` for age/position/experience. `season_type` is in the
key (mirroring `PlayerWeekStat`) so a postseason recompute never collides with the
regular-season row. The full summed usage dict lives in a `usage` JSONField so a
new Sleeper stat category never costs a migration — the same "promote the few
columns you sort on, keep the rest in JSON" pattern `PlayerWeekStat` uses for
`pts_*` + `stats`. Record this rationale in the model docstring.

Note in the docstring that this is **deterministic feature engineering**, not a
model: every field is a reproducible aggregation of `kind="stat"` rows, and it is
the *input* to the ML valuation in feature 006.

## Implementation plan

1. **`PlayerSeasonMetrics` model** in `apps/players/models.py`, after
   `PlayerWeekStat`. Inherit `TimeStampedModel` from `apps.core.models` (as
   `PlayerWeekStat` does) so PR 02's bulk upsert can refresh `updated_at`
   explicitly. The FK points at `Player` (the auto `id` PK — matching the
   `PlayerWeekStat` / `RosterSlot` pattern):

   ```python
   class PlayerSeasonMetrics(TimeStampedModel):
       """Derived, model-ready metrics for one player's season.

       Materialized by ``manage.py recompute_metrics`` from the realised
       ``PlayerWeekStat`` rows (``kind="stat"``) — deterministic feature
       engineering, NOT a prediction. One row per (player, season, season_type);
       the ML valuation in feature 006 joins these against ``Player`` for
       age/position/experience. The full summed usage dict is kept in ``usage``
       so a new Sleeper stat category never costs a migration; the few columns
       we sort/filter on are promoted.
       """

       player = models.ForeignKey(
           Player, on_delete=models.CASCADE, related_name="season_metrics"
       )
       season = models.PositiveSmallIntegerField()
       season_type = models.CharField(max_length=8, default="regular")
       # Denormalized from Player so per-season leaderboards filter without a join.
       position = models.CharField(max_length=8, blank=True)

       games_played = models.PositiveSmallIntegerField(default=0)

       # Season totals.
       total_ppr = models.FloatField(null=True, blank=True)
       total_half_ppr = models.FloatField(null=True, blank=True)
       total_std = models.FloatField(null=True, blank=True)

       # Per-game averages (total / games_played).
       ppg_ppr = models.FloatField(null=True, blank=True)
       ppg_half_ppr = models.FloatField(null=True, blank=True)
       ppg_std = models.FloatField(null=True, blank=True)

       # Consistency, all on weekly PPR points.
       stdev_ppr = models.FloatField(null=True, blank=True)   # population stdev
       floor_ppr = models.FloatField(null=True, blank=True)   # min weekly PPR
       ceiling_ppr = models.FloatField(null=True, blank=True)  # max weekly PPR

       # Recent form: average of the last RECENT_WINDOW played weeks, and the
       # delta vs the season average (positive = trending up).
       recent_ppg_ppr = models.FloatField(null=True, blank=True)
       form_delta_ppr = models.FloatField(null=True, blank=True)

       # Usage proxies, summed across the season. Promoted for sorting; the full
       # summed stat-category dict is in `usage`.
       targets = models.PositiveIntegerField(null=True, blank=True)   # rec_tgt
       carries = models.PositiveIntegerField(null=True, blank=True)   # rush_att
       snaps = models.PositiveIntegerField(null=True, blank=True)     # off_snp
       usage = models.JSONField(default=dict, blank=True)

       class Meta:
           unique_together = ("player", "season", "season_type")
           ordering = ["-season", "-ppg_ppr"]
           indexes = [
               models.Index(fields=["season", "position"]),
               models.Index(fields=["player", "season"]),
               models.Index(fields=["season", "-ppg_ppr"]),
           ]

       def __str__(self) -> str:
           return f"{self.player} {self.season} metrics"
   ```

   Notes to carry into the code:
   - `unique_together` is the idempotency key PR 02's
     `bulk_create(update_conflicts=True, unique_fields=[...])` upserts against.
   - Keep every derived measure **nullable** — a player with zero played weeks
     yields nulls for the ratios (division by zero is avoided in PR 02), not
     zeros, so 006 can tell "no data" apart from "genuinely zero".
   - `PositiveSmallIntegerField` for `season` (matches `PlayerWeekStat`);
     `games_played` fits `PositiveSmallIntegerField` (max 18-ish); usage counts
     use `PositiveIntegerField`.

2. **Migration** — name it; the number is auto-assigned:
   `make makemigrations ARGS="players --name add_playerseasonmetrics"`, then
   `make migrate`. Confirm it applies cleanly against the container's Postgres.
   This is the fourth `players` migration (after `0003_add_playerweekstat`).

3. **Admin** in `apps/players/admin.py` (mirror the existing `PlayerWeekStatAdmin`
   style): register `PlayerSeasonMetrics` with `list_display`
   (`player`, `season`, `position`, `games_played`, `ppg_ppr`, `form_delta_ppr`),
   `list_filter` (`season`, `position`, `season_type`), `search_fields`
   (`player__full_name`), and `raw_id_fields = ("player",)` so the change list
   doesn't render a dropdown of the whole player table.

## Testing

Add `apps/players/tests/test_metrics_models.py`. Build `Player` rows directly (a
teamless player is fine; no Sleeper calls) and construct `PlayerSeasonMetrics`
rows by hand. Cover:

- `test_str` renders `"<player> <season> metrics"`.
- `test_unique_together_enforced` — a second row with the same
  `(player, season, season_type)` raises `IntegrityError`, but the same
  `(player, season)` with a different `season_type` is allowed.
- `test_default_ordering` — rows come back newest-season first.
- `test_cascade_delete` — deleting a `Player` removes its metrics rows.
- `test_usage_jsonfield_roundtrips` — a nested usage dict is stored and read back
  intact; `usage` defaults to `{}`.
- `test_nullable_measures_default_to_none` — a row saved with only the key fields
  leaves the derived measures null (not zero).

Run narrowed: `make test ARGS="apps.players"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
