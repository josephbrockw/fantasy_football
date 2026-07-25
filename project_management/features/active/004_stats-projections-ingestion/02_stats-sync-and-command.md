# 02 — Stats client, backfill sync & command

Feature: `004_stats-projections-ingestion`

## Objective

Fetch weekly stats and projections from Sleeper and store them as
`PlayerWeekStat` rows, with a `sync_stats` management command and a
`make sync-stats` target that defaults to a **full historical backfill** (every
season × week × both endpoints) and can be narrowed by range. Writes are
idempotent bulk upserts wrapped in a `SyncRun`; unknown player ids are skipped.

## Scope

**In scope**
- `apps/sleeper/client.py` — `get_player_stats` / `get_player_projections` and a
  `StatsSource` protocol
- `apps/players/services.py` — `sync_stats` backfill service + bulk upsert +
  known-player filtering + `SyncRun` integration
- `apps/players/management/commands/sync_stats.py`
- `make sync-stats` target in the `Makefile`
- A trimmed JSON fixture + a `FakeSleeperClient` extension for tests

**Out of scope**
- The `PlayerWeekStat` model itself (PR 01)
- The coverage report command (PR 03)
- Back-filling `Player` rows for unknown stat ids — stats for players not in the
  table are **skipped**, not inserted (see rationale below)
- Any player-facing view

## Design decision: skip unknown player ids

Each week's payload is keyed by the **entire** Sleeper player universe (~550 KB,
thousands of ids including practice-squad and retired players), while the
`Player` table deliberately holds only ~1,043 live players plus anyone rostered.
Mirror `sync_trending`: resolve the known ids with one
`Player.objects.filter(sleeper_id__in=...)` query per week, write stats only for
those, and **count the rest as skipped**. This keeps stored volume bounded to
tracked players (see the volume note below) and avoids polluting the player
universe with retired names. (Unlike the league sync's `ensure_players_exist`,
there is no FK-integrity forcing function here — a skipped stat row simply isn't
stored.) If a future need arises to backfill players first, that is a
`make sync-players` / `make sync-league` concern, not this command's.

## Volume & idempotency (call out in the code)

~550 KB/week × ~18 weeks × N seasons × 2 kinds is fetched, but only the
known-player slice is stored: roughly 1,000 rows/week/kind. A ~8-season backfill
is on the order of 1,000 × 18 × 8 × 2 ≈ 290k rows — comfortable for Postgres.
Fetch is the cost, not storage. Two consequences the code must honour:
- **Idempotent re-runs.** Upsert on the `unique_together` key so re-running a
  range updates in place. Because `bulk_create(update_conflicts=True)` and
  `queryset.update()` **bypass** `TimeStampedModel.auto_now`, set `updated_at`
  explicitly on every instance before writing (the exact caveat `SyncRun` and
  `sync_players` already work around — see `CLAUDE.md`).
- **Empty weeks are normal.** A season/week Sleeper has no data for returns `{}`
  or `null`; treat that as zero rows, not an error, so a full backfill loop
  doesn't abort on the first gap (e.g. week 18 in pre-2021 seasons).

## Implementation plan

1. **Client methods** in `apps/sleeper/client.py`, in the `# --- endpoints`
   block, following the existing `_get` pattern:

   ```python
   def get_player_stats(
       self, season: str, week: int, season_type: str = "regular"
   ) -> dict[str, Any]:
       """Every player's actual stat line for one NFL week, keyed by player_id."""
       return self._get(f"stats/nfl/{season_type}/{season}/{week}") or {}

   def get_player_projections(
       self, season: str, week: int, season_type: str = "regular"
   ) -> dict[str, Any]:
       """Every player's projected stat line for one NFL week, keyed by player_id."""
       return self._get(f"projections/nfl/{season_type}/{season}/{week}") or {}
   ```

   The `or {}` mirrors the league helpers so a `null`/absent body is an empty
   dict, not `None`. Add a `StatsSource(Protocol)` (with `get_nfl_state`,
   `get_player_stats`, `get_player_projections`) alongside the existing
   capability protocols, and include it in the `SleeperAPI` union so
   `SleeperClient` still satisfies everything.

2. **Coercion + row builder** in `apps/players/services.py`. Reuse the existing
   `_as_int`/`_as_str` helpers; add an `_as_float` for the promoted scoring
   fields (Sleeper sends numbers, but coerce defensively → `None` on bad data).
   A builder that turns one `(player, season, week, kind, stat_dict)` into an
   unsaved `PlayerWeekStat`, setting `updated_at = timezone.now()` so the bulk
   upsert refreshes it:

   ```python
   def stat_row_from_payload(player, season, week, season_type, kind, stat_dict):
       return PlayerWeekStat(
           player=player,
           season=season,
           week=week,
           season_type=season_type,
           kind=kind,
           pts_ppr=_as_float(stat_dict.get("pts_ppr")),
           pts_half_ppr=_as_float(stat_dict.get("pts_half_ppr")),
           pts_std=_as_float(stat_dict.get("pts_std")),
           stats=stat_dict,
           updated_at=timezone.now(),
       )
   ```

3. **Per-week upsert** in `apps/players/services.py`, mirroring
   `upsert_players`:

   ```python
   STAT_UPDATE_FIELDS = ["pts_ppr", "pts_half_ppr", "pts_std", "stats", "updated_at"]

   def upsert_week_stats(rows: list[PlayerWeekStat]) -> int:
       if not rows:
           return 0
       PlayerWeekStat.objects.bulk_create(
           rows,
           batch_size=BATCH_SIZE,
           update_conflicts=True,
           unique_fields=["player", "season", "week", "season_type", "kind"],
           update_fields=STAT_UPDATE_FIELDS,
       )
       return len(rows)
   ```

4. **`ingest_week`** helper — given a client, `season`, `week`, `kind`,
   `season_type`: call the right client method, resolve known players once
   (`{p.sleeper_id: p for p in Player.objects.filter(sleeper_id__in=payload)}`),
   build rows for known ids and count unknowns as skipped, then
   `upsert_week_stats`. Return a small `(written, skipped)` tally. This is the
   single code path both `stat` and `projection` flow through.

5. **`sync_stats` service** — the backfill orchestrator. Signature roughly:

   ```python
   def sync_stats(
       client: StatsSource | None = None,
       *,
       start_season: int | None = None,
       end_season: int | None = None,
       weeks: Iterable[int] | None = None,
       kinds: Sequence[str] = ("stat", "projection"),
       season_type: str = "regular",
       dry_run: bool = False,
   ) -> SyncStats:
   ```
   - Add a `MIN_SEASON` module constant (the earliest season worth pulling —
     propose `2018`; document that Sleeper has older data but the player universe
     and ML relevance start here, and it's overridable via `--start-season`).
   - Default `end_season` to the current season from `client.get_nfl_state()`
     (`int(state["season"])`) so a no-arg run stops at the live season.
   - Default `weeks` to `range(1, 19)`.
   - Wrap the whole backfill in one `with SyncRun.track(SyncRun.Kind.STATS) as
     run:` and loop `season → week → kind`, accumulating written/skipped into the
     existing `SyncStats` dataclass; set `run.records_written` /
     `run.records_skipped` at the end. Empty payloads contribute zero and are not
     an error.
   - `dry_run` fetches and filters but calls no upsert (parity with
     `sync_players`).

6. **`sync_stats` management command** in
   `apps/players/management/commands/sync_stats.py`, modelled on `sync_players`
   / `sync_trending`:
   - `--start-season` / `--end-season` (ints; default to the backfill window),
   - `--season` (convenience: a single season, sets both bounds),
   - `--week` (repeatable or comma-list → restrict weeks; default all),
   - `--kind` (`stat` | `projection` | `both`, default `both`),
   - `--season-type` (default `regular`),
   - `--dry-run`.
   Translate options into the `sync_stats` kwargs, catch `SleeperAPIError` →
   `CommandError`, and print an `f"Wrote {written} stat row(s); skipped
   {skipped} unknown/no-data."` success line via `self.style.SUCCESS`.

7. **Makefile** — add a `sync-stats` target next to `sync-trending`, and add
   `sync-stats` to the `.PHONY` list at the top:

   ```make
   sync-stats:  ## Backfill weekly player stats & projections
   	$(EXEC) python manage.py sync_stats $(ARGS)
   ```
   With no `ARGS` it runs the full backfill; e.g.
   `make sync-stats ARGS="--season 2025 --kind projection"` narrows it.

8. **Fixtures** — add
   `apps/players/tests/fixtures/player_stats_sample.json`, a hand-trimmed week
   payload keyed by `player_id`: a couple of ids that exist in
   `players_sample.json` (so they're "known") plus one id that does **not** (the
   skip case), each with a small realistic stat dict including `pts_ppr` /
   `pts_half_ppr` / `pts_std`. A projections variant can reuse the same shape.
   Extend `apps/players/tests/utils.py` `FakeSleeperClient` with
   `get_player_stats` / `get_player_projections` (recording calls, returning the
   fixture; support returning `{}` for an "empty week" and honouring `self._error`
   like the existing methods) and a `get_nfl_state` that already exists.

## Testing

In `apps/players/tests/test_stats_services.py` and an addition to
`test_commands.py`:

- `test_ingest_week_writes_known_skips_unknown` — the known ids land as
  `PlayerWeekStat` rows with promoted `pts_ppr` etc.; the unknown id is counted
  in `skipped` and not stored (the regression guard for the volume decision).
- `test_sync_stats_writes_both_kinds` — a run with default kinds stores both a
  `stat` and a `projection` row for the same `(player, season, week)`, and they
  coexist (distinct `kind`).
- `test_sync_stats_is_idempotent` — run the same range twice: no duplicate rows,
  and a changed stat value (mutate the fixture between runs) is updated in place;
  assert `updated_at` advanced (proves the explicit-`auto_now` workaround).
- `test_sync_stats_empty_week_is_not_an_error` — client returns `{}` for a week;
  the run completes, writes zero for it, and the `SyncRun` is `success`.
- `test_sync_stats_default_range_uses_nfl_state` — with `get_nfl_state` reporting
  season 2025, a no-arg backfill iterates `MIN_SEASON..2025` (assert the season
  span the fake client was asked for).
- `test_sync_stats_records_failure` — client raises; `SyncRun.status == "failed"`
  and the error is captured, no partial success recorded.
- `test_dry_run_writes_nothing`.
- Command tests: `test_sync_stats_command` (invoke via `call_command`, assert the
  success line and rows written), `test_sync_stats_command_kind_flag`
  (`--kind projection` writes only projection rows),
  `test_sync_stats_command_season_flag` (`--season 2025` bounds both ends), and
  `test_sync_stats_command_wraps_api_error` (`SleeperAPIError` → `CommandError`).
- `apps/sleeper/tests/test_client.py` — add cases for `get_player_stats` /
  `get_player_projections` hitting the right path and turning a `null` body into
  `{}`, with `requests` mocked. **No test hits the network.**
- Manual: `make sync-stats ARGS="--season 2024 --week 1"` then a shell check that
  `PlayerWeekStat.objects.filter(kind="stat").exists()` and the promoted
  `pts_ppr` is populated.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
