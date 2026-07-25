# 01 — PlayerWeekStat model & migration

Feature: `004_stats-projections-ingestion`

## Objective

Add the storage layer for weekly stats and projections — one `PlayerWeekStat`
model with a `kind` discriminator — plus its migration, admin registration, and
the new `SyncRun.Kind.STATS` audit kind. No client calls, no sync, no command:
a reviewable data-model foundation that PR 02 fills.

## Scope

**In scope**
- `PlayerWeekStat` model in `apps/players/models.py`
- New `SyncRun.Kind.STATS` choice in `apps/sleeper/models.py`
- Migrations for both apps
- Admin registration in `apps/players/admin.py`
- Model-level tests

**Out of scope**
- The Sleeper client methods and the sync service (PR 02)
- The `sync_stats` management command and `make sync-stats` (PR 02)
- The coverage report command (PR 03)
- Any player-facing view

## Design decision: one table, not two

Stats and projections come from **parallel endpoints with an identical payload
shape** — both are `{player_id: {stat_category: value, ...}}`, both are keyed the
same way, both promote the same `pts_ppr` / `pts_half_ppr` / `pts_std` scoring
fields, and the ML feature will want to join a projection against the actual
result for the same `(player, season, week)`. A single table with a
`kind` (`stat` | `projection`) discriminator gives that join for free, keeps one
set of indexes/migrations/upsert logic, and lets the sync service in PR 02 write
both endpoints through one code path. Two tables would duplicate all of that for
no query benefit. Record this rationale in the model docstring.

## Implementation plan

1. **Add the `SyncRun` kind** in `apps/sleeper/models.py` — extend the
   `SyncRun.Kind` `TextChoices` with `STATS = "stats", "Stats"` (mirroring the
   existing `PLAYERS` / `LEAGUE` / `TRENDING` entries). This is what PR 02's sync
   wraps its run in. Generate the migration for `apps/sleeper` (choices changes
   still produce a migration).

2. **`PlayerWeekStat` model** in `apps/players/models.py`. Inherit
   `TimeStampedModel` from `apps.core.models` (as `SyncRun` does) so bulk upserts
   in PR 02 can refresh `updated_at` explicitly. The FK points at `Player` (auto
   `id` PK — `sleeper_id` is the unique key, not the PK, so the FK is on the row
   id, matching the `Target`/`RosterSlot` pattern):

   ```python
   class PlayerWeekStat(TimeStampedModel):
       """One player's stat or projection line for a single NFL week.

       Fed by ``manage.py sync_stats`` from Sleeper's parallel
       ``/stats`` and ``/projections`` endpoints. One table, discriminated by
       ``kind``, because the two payloads are identically shaped and the ML
       feature will join a projection against the realised stat for the same
       (player, season, week). The full Sleeper stat-category dict is kept in
       ``stats`` so a new category never costs a migration; the three fantasy
       scoring totals are promoted to nullable columns for sorting/aggregation.
       """

       class Kind(models.TextChoices):
           STAT = "stat", "Stat"
           PROJECTION = "projection", "Projection"

       player = models.ForeignKey(
           Player, on_delete=models.CASCADE, related_name="week_stats"
       )
       season = models.PositiveSmallIntegerField()
       week = models.PositiveSmallIntegerField()
       # Sleeper path segment: regular | post | pre. Default matches the backfill.
       season_type = models.CharField(max_length=8, default="regular")
       kind = models.CharField(max_length=12, choices=Kind.choices)

       pts_ppr = models.FloatField(null=True, blank=True)
       pts_half_ppr = models.FloatField(null=True, blank=True)
       pts_std = models.FloatField(null=True, blank=True)

       # Whole stat-category dict, so a Sleeper schema addition never migrates.
       stats = models.JSONField(default=dict, blank=True)

       class Meta:
           unique_together = ("player", "season", "week", "season_type", "kind")
           ordering = ["-season", "-week", "kind"]
           indexes = [
               models.Index(fields=["season", "week", "kind"]),
               models.Index(fields=["player", "kind"]),
               models.Index(fields=["kind", "season", "week"]),
           ]

       def __str__(self) -> str:
           return f"{self.player} {self.season} W{self.week} {self.kind}"
   ```

   Notes to carry into the code:
   - `unique_together` is the idempotency key PR 02's
     `bulk_create(update_conflicts=True, unique_fields=[...])` upserts against.
     `season_type` is in the key so a future postseason pull cannot collide with
     the regular-season row for the same week.
   - Use `PositiveSmallIntegerField` for `season`/`week` — seasons fit, weeks are
     1–18. Keep them as integers (the sync coerces Sleeper's string path args).

3. **Migrations** — always name them; the number is auto-assigned:
   `make makemigrations ARGS="players --name add_playerweekstat"` and
   `make makemigrations ARGS="sleeper --name add_stats_synckind"` (the choices-only
   `SyncRun.Kind` change), then `make migrate`. Confirm both apply cleanly against
   the container's Postgres.

4. **Admin** in `apps/players/admin.py` (mirror the existing `Player` admin
   style): register `PlayerWeekStat` with `list_display`
   (`player`, `season`, `week`, `kind`, `pts_ppr`), `list_filter`
   (`kind`, `season`, `week`, `season_type`), `search_fields` on the player name
   (`player__full_name`), and `raw_id_fields = ("player",)` so the change list
   doesn't try to render a dropdown of the whole player table.

## Testing

Add `apps/players/tests/test_stats_models.py` (or extend `test_models.py`). Use a
small `Player`-creating helper (a teamless player is fine; no Sleeper calls).
Cover:

- `test_playerweekstat_str` renders `"<player> <season> W<week> <kind>"`.
- `test_unique_together_enforced` — a second row with the same
  `(player, season, week, season_type, kind)` raises `IntegrityError`, but the
  same `(player, season, week)` with a different `kind` (stat vs projection) is
  allowed.
- `test_default_ordering` — rows come back newest-season/week first.
- `test_cascade_delete` — deleting a `Player` removes its `PlayerWeekStat` rows.
- `test_stats_jsonfield_roundtrips` — a nested stat dict is stored and read back
  intact; `stats` defaults to `{}`.
- `apps/sleeper` model test asserting `SyncRun.Kind.STATS == "stats"`.

Run narrowed: `make test ARGS="apps.players"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
