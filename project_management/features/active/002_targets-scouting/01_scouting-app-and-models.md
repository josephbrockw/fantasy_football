# 01 — Scouting app & models

Feature: `002_targets-scouting`

## Objective

Stand up the `apps/scouting/` app and its two user-owned models — `Target`
(a stance on a player) and `ScoutingNote` (a dated free-form observation) — with
migrations and admin registration. No UI. A reviewable foundation that PRs 02
and 03 build the boards on.

## Scope

**In scope**
- New `apps/scouting/` app, wired into `INSTALLED_APPS`
- `Target` and `ScoutingNote` models on the 001 `Player`
- Migration + admin registration for both
- Model-level tests

**Out of scope**
- Any view, URL, or template — both boards are PRs 02 and 03
- Inline management endpoints (built in PR 02, reused by PR 03)
- Any change to `apps/players`, `apps/leagues`, or the Sleeper client

## Implementation plan

1. **Create the app** matching the existing per-domain layout
   (`apps/players`, `apps/leagues`, `apps/sleeper`):
   - `make manage ARGS="startapp scouting"` (or `python manage.py startapp`),
     then move it under `apps/` so the package is `apps.scouting`.
   - `apps/scouting/apps.py` → `ScoutingConfig` with
     `default_auto_field = "django.db.models.BigAutoField"`,
     `name = "apps.scouting"`, `label = "scouting"` (mirror
     `apps/players/apps.py`).
   - Ensure `apps/scouting/migrations/__init__.py` exists.
   - Add `"apps.scouting"` to `INSTALLED_APPS` in `config/settings.py`, after
     `"apps.leagues"`.
2. **Models** in `apps/scouting/models.py`. Both inherit
   `TimeStampedModel` from `apps.core.models`; FKs point at
   `apps.players.models.Player` (auto `id` PK — `sleeper_id` is the unique key,
   not the PK, so the FK is on the row id):

   ```python
   class Target(TimeStampedModel):
       class Stance(models.TextChoices):
           ACQUIRE = "acquire", "Acquire"
           AVOID = "avoid", "Avoid"

       class Priority(models.TextChoices):
           HIGH = "high", "High"
           MEDIUM = "medium", "Medium"
           LOW = "low", "Low"

       player = models.OneToOneField(
           Player, on_delete=models.CASCADE, related_name="target"
       )
       stance = models.CharField(max_length=8, choices=Stance.choices)
       tier = models.PositiveSmallIntegerField(null=True, blank=True)  # 1 = top tier
       priority = models.CharField(
           max_length=8, choices=Priority.choices, default=Priority.MEDIUM
       )
       notes = models.TextField(blank=True)  # short summary note
   ```

   ```python
   class ScoutingNote(TimeStampedModel):
       player = models.ForeignKey(
           Player, on_delete=models.CASCADE, related_name="scouting_notes"
       )
       body = models.TextField()

       class Meta:
           ordering = ["-created_at"]  # newest first
   ```

   Rationale (carry into the docstrings): `Target` is `OneToOne` because there is
   exactly one stance per player, so "set stance" is an `update_or_create` and
   clearing a stance is a delete. `ScoutingNote` is a separate one-to-many dated
   log — distinct from `Target.notes`, which is a single quick summary shown on
   the target row. Single-user app: no per-user scoping beyond the existing
   `Manager.is_me`, which only the Targets board (PR 03) uses, and only to label
   my roster versus a rival's.
   Give each model a `__str__` (e.g. `f"{self.player} — {self.stance}"` and the
   note's truncated body).
3. **Migration** — `make makemigrations` then `make migrate`. Confirm the
   migration is created under `apps/scouting/migrations/0001_initial.py` and
   applies cleanly against the container's Postgres.
4. **Admin** in `apps/scouting/admin.py` (mirror `apps/leagues/admin.py`
   style): register `Target` (`list_display` of player, stance, tier, priority;
   `list_filter` on stance/priority) and `ScoutingNote` (player, truncated body,
   created_at). Optionally add a `ScoutingNoteInline` — keep it simple; the admin
   is a fallback, not the primary UI.

## Testing

Add `apps/scouting/tests/__init__.py` and
`apps/scouting/tests/test_models.py`. Use a small `Player`-creating helper
(a teamless/rookie player is fine; no Sleeper calls). Cover:

- `test_target_str` and `test_scouting_note_str` render sensibly.
- `test_target_is_one_to_one_per_player` — creating a second `Target` for the
  same `player` raises `IntegrityError`.
- `test_target_priority_defaults_to_medium` — a `Target` saved without a
  priority reads back `Priority.MEDIUM`; `tier` may be null.
- `test_target_stance_choices` — `acquire`/`avoid` are the accepted stances.
- `test_scouting_notes_default_ordering_newest_first` — two notes on one player
  come back in `-created_at` order.
- `test_cascade_delete` — deleting a `Player` removes its `Target` and
  `ScoutingNote` rows.

Run the narrowed suite: `make test ARGS="apps.scouting"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
