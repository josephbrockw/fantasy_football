# 02 — Recompute service, command & make target

Feature: `005_player-analytics-layer`

## Objective

Fill the `PlayerSeasonMetrics` table: a deterministic `recompute_metrics` service
that aggregates `PlayerWeekStat` (`kind="stat"`) rows into per-season metrics, a
`recompute_metrics` management command, and a `make recompute-metrics` target.
Reads the local DB only — **no Sleeper network calls** — and wraps each run in a
`SyncRun` (new `metrics` kind) for audit parity with the sync commands.

## Scope

**In scope**
- New `SyncRun.Kind.METRICS` choice in `apps/sleeper/models.py` + its migration
- Recompute functions in `apps/players/services.py`
- `apps/players/management/commands/recompute_metrics.py`
- `recompute-metrics` target in the `Makefile` (+ `.PHONY`)
- Service and command tests

**Out of scope**
- Any change to the `PlayerSeasonMetrics` schema (PR 01) or `PlayerWeekStat`
- The read-only report command (PR 03)
- Projection-based metrics — this PR aggregates realised stats only; joining a
  projection against the actual is 006's job
- Cross-season rolling windows and historical age — see "Deliberately simple"

## Design decision: recompute from the DB, wrapped in SyncRun

The recompute reads `PlayerWeekStat` and writes `PlayerSeasonMetrics`; it never
touches Sleeper. It still belongs in a `SyncRun` because that is the repo's audit
surface for batch jobs that write rows — it records `records_written` /
`records_skipped` and captures failure without leaving a half-recorded run,
exactly as `sync_stats` does. Add a `METRICS` kind so the audit log distinguishes
a local recompute from a network sync. Reuse the existing `SyncStats` dataclass
and the `bulk_create(update_conflicts=True)` upsert pattern already established in
`services.py` — one code path, idempotent re-runs.

## Deliberately simple (documented limitations)

- **Metrics come from `kind="stat"` only.** Projections are ignored here.
- **Recent form is within-season.** `recent_ppg_ppr` uses the last
  `RECENT_WINDOW` played weeks of that season; a window spanning the season
  boundary is a future refinement, noted in the service docstring.
- **Age/experience are read live from `Player`,** not snapshotted per season —
  deriving a historical per-season age is out of scope. Only `position` is
  denormalized onto the metrics row (for leaderboard filtering).
- **"Played" is defined by a non-null `pts_ppr`.** Sleeper omits/nulls fantasy
  points for a week a player did not play; counting those weeks would deflate
  per-game averages. Document this rule.

## Implementation plan

1. **Add the `SyncRun` kind** in `apps/sleeper/models.py` — extend
   `SyncRun.Kind` with `METRICS = "metrics", "Metrics"` (mirroring the existing
   `STATS` entry). Generate the migration:
   `make makemigrations ARGS="sleeper --name add_metrics_synckind"`, then
   `make migrate`.

2. **Constants** at the top of the metrics section in `apps/players/services.py`:
   - `RECENT_WINDOW = 4` — number of trailing played weeks for recent form.
   - Sleeper usage keys: `TARGETS_KEY = "rec_tgt"`, `CARRIES_KEY = "rush_att"`,
     `SNAPS_KEY = "off_snp"`.
   - `METRICS_UPDATE_FIELDS` — every computed column plus `usage`, `position`,
     and `updated_at` (the upsert's `update_fields`, mirroring
     `STAT_UPDATE_FIELDS`).

3. **Pure aggregation helper** `metrics_from_week_rows(player, season,
   season_type, rows)` returning an unsaved `PlayerSeasonMetrics`, where `rows`
   is that player-season's `PlayerWeekStat` list ordered by `week`. It:
   - Selects **played** weeks (`row.pts_ppr is not None`); `games_played` is that
     count. If zero played weeks, return a row with `games_played=0` and null
     measures (guard every division).
   - Sums `pts_ppr` / `pts_half_ppr` / `pts_std` over played weeks → `total_*`;
     divides by `games_played` → `ppg_*`.
   - `stdev_ppr` = population standard deviation of the played weeks' `pts_ppr`
     via `statistics.pstdev` (needs ≥1 value; `pstdev` of a single value is 0.0).
   - `floor_ppr` = `min(...)`, `ceiling_ppr` = `max(...)` of played-week PPR.
   - `recent_ppg_ppr` = mean of the **last `RECENT_WINDOW`** played weeks' PPR
     (fewer than the window → mean of what exists); `form_delta_ppr` =
     `recent_ppg_ppr - ppg_ppr`.
   - Usage: sum each numeric key across `row.stats` for played weeks into a
     `usage` dict (reuse `_as_float`/`_as_int`-style coercion; skip the `pts_*`
     keys, which are promoted elsewhere). Promote `usage[TARGETS_KEY]` →
     `targets`, `usage[CARRIES_KEY]` → `carries`, `usage[SNAPS_KEY]` → `snaps`
     (null when absent, coerced to int).
   - Set `position` from `player.position`, `updated_at = timezone.now()` (the
     `auto_now` bypass, as `stat_row_from_payload` does).

4. **`recompute_season(season, season_type, *, dry_run)`** — fetch every
   `PlayerWeekStat` for that `(season, season_type, kind="stat")` with
   `select_related("player")`, ordered by `player_id, week`; group by `player`
   (e.g. `itertools.groupby`); build a metrics row per group via the helper.
   Returns the built list (and, when not `dry_run`, upserts it via a
   `bulk_create(..., update_conflicts=True, unique_fields=["player", "season",
   "season_type"], update_fields=METRICS_UPDATE_FIELDS)`, mirroring
   `upsert_week_stats`). Players with only unknown/no-data weeks contribute no
   group and are effectively skipped.

5. **`recompute_metrics(*, seasons=None, season_type="regular", dry_run=False)
   -> SyncStats`** — the orchestrator, mirroring `sync_stats`'s shape:
   - Wrap in `SyncRun.track(SyncRun.Kind.METRICS)`.
   - `seasons` defaults to the distinct seasons present in `PlayerWeekStat`
     (`values_list("season", flat=True).distinct()`) — no network, no
     `get_nfl_state` call needed since the data is already local.
   - For each season call `recompute_season`; accumulate `written` (rows upserted)
     into `stats.written`. Record `run.records_written` / `records_skipped`.

6. **Management command** `apps/players/management/commands/recompute_metrics.py`,
   modelled on `sync_stats.py`:
   - `--season` (single) / `--start-season` / `--end-season` to narrow, or a
     comma `--seasons` list; `--season-type` (default `regular`); `--dry-run`.
   - Call `recompute_metrics(...)`; there is no `SleeperAPIError` path (no
     network), but still surface unexpected failures via the `SyncRun` audit.
   - Print `self.style.SUCCESS(f"Recomputed {stats.written} season-metric
     row(s).")`.

7. **Makefile** — add a target beside `sync-stats` and list it in `.PHONY`:

   ```make
   recompute-metrics:  ## Rebuild PlayerSeasonMetrics from ingested stats
   	$(EXEC) python manage.py recompute_metrics $(ARGS)
   ```

## Testing

Add `apps/players/tests/test_metrics_services.py`. No network — create `Player`
rows and `PlayerWeekStat` rows directly (or reuse `sync_players` +
`sync_stats(client=FakeSleeperClient(), ...)` from `tests/utils.py` to seed a
known week, then add extra weeks by hand for the multi-week assertions).

- `test_pure_helper_computes_per_game_and_consistency` — feed
  `metrics_from_week_rows` a hand-built list of weekly PPR values (e.g.
  `[10, 20, 30]`) and assert `games_played`, `total_ppr`, `ppg_ppr` (20.0),
  `stdev_ppr` (`pstdev` = ~8.165), `floor_ppr` (10), `ceiling_ppr` (30).
- `test_recent_form_delta` — with more than `RECENT_WINDOW` weeks, assert
  `recent_ppg_ppr` averages only the last `RECENT_WINDOW` and `form_delta_ppr`
  equals `recent_ppg_ppr - ppg_ppr` (sign correct for an up-trend).
- `test_unplayed_weeks_excluded` — a week with `pts_ppr=None` does not count
  toward `games_played` or the averages.
- `test_zero_played_weeks_yields_nulls` — all-null weeks → `games_played=0` and
  null ratios, no `ZeroDivisionError`.
- `test_usage_proxies_summed` — `rec_tgt` / `rush_att` / `off_snp` across weeks
  sum into `targets` / `carries` / `snaps`; a week missing a key is tolerated;
  absent-everywhere → null; `usage` JSON holds the summed dict.
- `test_recompute_is_idempotent` — run `recompute_metrics` twice over the same
  season; row count is stable (upserted, not duplicated) and `updated_at`
  advances on the second run.
- `test_recompute_uses_only_stat_kind` — a `projection` row for the same
  player-week does not alter the metrics (only `kind="stat"` is read).
- `test_recompute_wraps_syncrun` — a run creates a `SyncRun` with
  `kind=SyncRun.Kind.METRICS` and `status=SUCCESS`; assert
  `SyncRun.Kind.METRICS == "metrics"`.
- `test_dry_run_writes_nothing` — `dry_run=True` stores no rows but reports a
  positive `written` count.
- Command test in `test_commands.py`: `call_command("recompute_metrics",
  "--season", "2024")` writes rows and prints the success line.

Run narrowed: `make test ARGS="apps.players"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
