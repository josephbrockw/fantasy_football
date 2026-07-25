# 01 — PlayerProfile model, enrichment app & migration

Feature: `011_external-data-enrichment`

## Objective

Add the storage layer for external per-player enrichment — a single
`PlayerProfile` model (OneToOne to `Player`) carrying NFL draft capital, the
external crosswalk ids downstream joins need, and nullable athleticism
measurables — plus a new `apps/enrichment` app, its migration, admin
registration, and the new `SyncRun.Kind.PROFILES` audit kind. No loader, no
sync, no command: a reviewable data-model foundation that PR 02 (draft capital)
and PR 03 (combine athleticism) fill.

## Scope

**In scope**
- New Django app `apps/enrichment` (config + `INSTALLED_APPS` registration)
- `PlayerProfile` model in `apps/enrichment/models.py`
- New `SyncRun.Kind.PROFILES` choice in `apps/sleeper/models.py`
- Migrations for both apps
- Admin registration in `apps/enrichment/admin.py`
- Model-level tests

**Out of scope**
- The file loader/client and the `sync_profiles` service (PR 02)
- Populating any field / the `make sync-profiles` command (PR 02)
- Combine athleticism join (PR 03)
- Any player-facing view (surfacing is admin-only for the whole feature)

## Design decisions

- **One OneToOne enrichment table, not columns on `Player`.** `Player` is a
  faithful mirror of the Sleeper payload (it even keeps the whole `raw` blob so a
  Sleeper schema change never migrates). External data comes from a *different*
  source on a *different* cadence, so it lives in its own table keyed to `Player`
  — mirroring how `PlayerWeekStat` hangs weekly data off `Player` rather than
  widening it. OneToOne (not FK) because there is exactly one profile per player.
- **FK on the row `id`, joined by `sleeper_id`.** `Player.sleeper_id` is the
  unique business key but *not* the PK; the OneToOne points at the auto `id` PK
  (the `PlayerWeekStat` / `Target` / `RosterSlot` pattern). The **ingest**
  resolves incoming rows to a `Player` via `sleeper_id` (PR 02); the model just
  stores the relation.
- **Store crosswalk ids we'll re-join on.** Keep `pfr_id` and `gsis_id` on the
  profile: PR 03 joins the nflverse combine release onto these rows via `pfr_id`,
  and downstream features may want a stable non-Sleeper id. They are captured in
  PR 02 from `db_playerids`.
- **Draft `pick` is the overall pick number.** `db_playerids` exposes both
  `draft_round` and the overall pick (`draft_ovr`/`draft_pick`); store the
  overall number in `draft_pick` (round-relative can always be derived later).
- **Athleticism is all nullable.** The combine covers only a subset of players
  (and only those who attended), so every measurable is nullable and stays null
  until PR 03 fills it.

## Implementation plan

1. **Create the app.** Add `apps/enrichment/` with `__init__.py`, an `apps.py`
   (`class EnrichmentConfig(AppConfig)`, `name = "apps.enrichment"`,
   `label = "enrichment"`, `default_auto_field = "django.db.models.BigAutoField"`
   — mirror `apps/players/apps.py`), `models.py`, `admin.py`, an empty
   `migrations/` package, and a `tests/` package (`__init__.py`,
   `fixtures/` dir). Register `"apps.enrichment"` in `INSTALLED_APPS` in
   `config/settings.py` (after `"apps.players"`).

2. **Add the `SyncRun` kind** in `apps/sleeper/models.py` — extend the
   `SyncRun.Kind` `TextChoices` with `PROFILES = "profiles", "Profiles"`
   (alongside `PLAYERS` / `LEAGUE` / `TRENDING` / `TRANSACTIONS` / `STATS`).
   This is what PR 02's sync wraps its run in. Generate the migration for
   `apps/sleeper` (a choices change still produces one).

3. **`PlayerProfile` model** in `apps/enrichment/models.py`. Inherit
   `TimeStampedModel` from `apps.core.models` (as `PlayerWeekStat` and `SyncRun`
   do) so PR 02's bulk upsert can refresh `updated_at` explicitly (the
   `auto_now` bypass caveat). Shape:

   ```python
   class PlayerProfile(TimeStampedModel):
       """External, non-Sleeper enrichment for a Player.

       Populated from versioned CSV release files (DynastyProcess db_playerids
       for draft capital + the sleeper_id crosswalk; nflverse combine for
       athleticism), NOT from Sleeper and NOT by scraping. One row per player.
       Draft capital is the strongest dynasty signal for young/unproven players
       and feeds features 005-007. All measurables are nullable — the combine
       covers only a subset of players.
       """

       player = models.OneToOneField(
           "players.Player", on_delete=models.CASCADE, related_name="profile"
       )

       # --- NFL draft capital (DynastyProcess db_playerids) ---
       draft_year = models.PositiveSmallIntegerField(null=True, blank=True)
       draft_round = models.PositiveSmallIntegerField(null=True, blank=True)
       # Overall pick number (draft_ovr), not round-relative.
       draft_pick = models.PositiveSmallIntegerField(null=True, blank=True)
       draft_team = models.CharField(max_length=8, blank=True)

       # --- external crosswalk ids (for re-joining other releases) ---
       pfr_id = models.CharField(max_length=16, blank=True)   # combine join key
       gsis_id = models.CharField(max_length=16, blank=True)

       # --- athleticism / combine measurables (nflverse combine, PR 03) ---
       height_inches = models.PositiveSmallIntegerField(null=True, blank=True)
       weight_lbs = models.PositiveSmallIntegerField(null=True, blank=True)
       bmi = models.FloatField(null=True, blank=True)
       forty = models.FloatField(null=True, blank=True)
       bench = models.PositiveSmallIntegerField(null=True, blank=True)
       vertical = models.FloatField(null=True, blank=True)
       broad_jump = models.PositiveSmallIntegerField(null=True, blank=True)
       cone = models.FloatField(null=True, blank=True)
       shuttle = models.FloatField(null=True, blank=True)

       # Which release(s) last touched this row — cheap provenance for debugging.
       source = models.CharField(max_length=32, blank=True)

       class Meta:
           indexes = [
               models.Index(fields=["draft_year"]),
               models.Index(fields=["draft_round"]),
               models.Index(fields=["pfr_id"]),
           ]

       def __str__(self) -> str:
           return f"{self.player} profile"

       @property
       def draft_capital_label(self) -> str:
           """e.g. '2023 R1.05' — a compact draft-capital badge for later UI."""
           if not self.draft_year:
               return ""
           if self.draft_round and self.draft_pick:
               return f"{self.draft_year} R{self.draft_round}.{self.draft_pick:02d}"
           return str(self.draft_year)
   ```

   Notes to carry into the code:
   - `OneToOneField` already implies `unique`, giving PR 02's
     `bulk_create(update_conflicts=True, unique_fields=["player"])` its
     idempotency key.
   - Keep the reference to `Player` as the string `"players.Player"` so the
     `enrichment` app doesn't import `players` at module load (one-way
     dependency: `enrichment` knows `players`, not vice-versa).

4. **Migrations** — always name them; the number is auto-assigned:
   `make makemigrations ARGS="enrichment --name add_playerprofile"` and
   `make makemigrations ARGS="sleeper --name add_profiles_synckind"`, then
   `make migrate`. Confirm both apply cleanly against the container's Postgres.

5. **Admin** in `apps/enrichment/admin.py` (mirror `PlayerWeekStatAdmin`):
   register `PlayerProfile` with `list_display`
   (`player`, `draft_year`, `draft_round`, `draft_pick`, `draft_team`, `forty`),
   `list_filter` (`draft_year`, `draft_round`, `draft_team`), `search_fields` on
   the player name (`player__full_name`, `pfr_id`, `gsis_id`), and
   `raw_id_fields = ("player",)` so the change list doesn't render a dropdown of
   the whole player table. `readonly_fields = ("created_at", "updated_at")`.

## Testing

Add `apps/enrichment/tests/test_models.py`. Use a small `Player`-creating helper
(a teamless player is fine; no external calls). Cover:

- `test_str` renders `"<player> profile"`.
- `test_one_profile_per_player` — a second `PlayerProfile` for the same `Player`
  raises `IntegrityError` (the OneToOne guard PR 02's upsert relies on).
- `test_cascade_delete` — deleting a `Player` removes its `PlayerProfile`.
- `test_measurables_default_null` — a profile created with only draft fields set
  reads back `forty` / `bench` / … as `None`.
- `test_draft_capital_label` — `2023 R1.05` for a set profile; `""` when
  `draft_year` is null.
- `apps/sleeper` model test asserting `SyncRun.Kind.PROFILES == "profiles"`.

Run narrowed: `make test ARGS="apps.enrichment"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
