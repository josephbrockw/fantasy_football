# 02 — Sleeper client & player sync

Feature: `001_sleeper-foundation`

## Objective

A reusable Sleeper API client and the `Player` table, populated by a
`sync_players` command that stores only live, fantasy-relevant players.

## Scope

**In scope**
- `apps/sleeper/client.py` — HTTP client for the read-only Sleeper API
- `apps/sleeper/models.py` — `SyncRun` audit log
- `apps/players/models.py` — `Player`
- `apps/players/admin.py` — searchable admin for eyeballing the data
- `manage.py sync_players`
- JSON fixtures trimmed from real payloads, for tests

**Out of scope**
- League, roster, or manager models (PR 03)
- Weekly stats / projections ingestion (backlog)
- Any player-facing view (PR 04/05)

## Implementation plan

1. **`apps/sleeper/client.py`** — a `SleeperClient` class, base
   `https://api.sleeper.app/v1`, built on a `requests.Session` with a
   `urllib3.Retry` adapter (3 retries, backoff, retry on 429/5xx) and an explicit
   connect/read timeout. Methods needed now: `get_nfl_state()`,
   `get_all_players()`, `get_trending_players(kind, lookback_hours, limit)`.
   Raise a `SleeperAPIError` on non-2xx. No auth — the API is public.
   Respect the documented ceiling of 1000 calls/min.
2. **`SyncRun` model** — `kind` (choices: `players`, `league`, …), `started_at`,
   `finished_at`, `status` (`running`/`success`/`failed`), `records_written`,
   `error`. Gives the once-per-day throttle a place to check and makes failures
   visible. Add a `classmethod last_success(kind)`.
3. **`Player` model** — `sleeper_id` (unique, indexed) as the natural key, plus
   typed columns: `first_name`, `last_name`, `full_name`, `search_full_name`,
   `position`, `fantasy_positions` (JSON), `team`, `status`, `active`, `age`,
   `birth_date`, `years_exp`, `rookie_year`, `college`, `height`, `weight`,
   `number`, `depth_chart_position`, `depth_chart_order`, `injury_status`,
   `injury_body_part`, `search_rank`, and `raw` (JSONField, the whole payload so a
   Sleeper schema change never costs a migration). Indexes on `position`, `team`,
   `active`, `search_full_name`. Inherit `TimeStampedModel`.
4. **Ingest filter** — in `apps/players/services.py`:
   ```python
   FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

   def is_live_player(payload: dict) -> bool:
       return (
           bool(payload.get("team"))
           and payload.get("position") in FANTASY_POSITIONS
       )
   ```
   **Do not filter on `active`.** Sleeper reports Tom Brady, Drew Brees, Antonio
   Brown, Todd Gurley and Ezekiel Elliott as `active: true` with
   `status: "Active"`; `team` is the only trustworthy liveness signal. Measured
   against the live dump this keeps 1,043 of 12,200 players — all 32 NFL teams
   (29–37 players each), all 32 `DEF` entries, and 225 of the 233 2026 rookies.
5. **Normalisation** — `rookie_year` comes from `metadata.rookie_year` (a string)
   and needs an int cast; `height`/`weight` are strings; `search_rank` uses `999`
   and `9999999` as sentinels (1,436 players sit on the latter) and must be stored
   as `NULL` in those cases. Document that `search_rank` is a coarse
   autocomplete-ordering hint with heavy collisions, **not an ADP** — real
   valuation is the ML feature's job.
6. **`sync_players` command** — fetch the dump, filter, then upsert in batches with
   `bulk_create(update_conflicts=True, unique_fields=["sleeper_id"])`. Wrap in a
   `SyncRun`. Flags: `--force` (bypass the once-per-day throttle that the docs ask
   for), `--include-inactive` (skip the filter and store everything), `--dry-run`.
   Report counts written/skipped to stdout.
7. **Fixtures** — `apps/players/tests/fixtures/players_sample.json`, hand-trimmed
   from the real dump to a handful of records: an active starter, a `DEF`, an
   offensive lineman (wrong position), a 2026 rookie, and Tom Brady
   (`team: null`, `active: true`) as the retired-player case.

## Testing

- `test_is_live_player` — table-driven over the fixture: starter and `DEF` kept;
  lineman, Brady, and a `team: null` rookie rejected. This is the regression guard
  for the `active`-flag trap.
- `test_sync_players_writes_expected_rows` — client mocked to return the fixture;
  assert only the live players land and field mapping is correct.
- `test_sync_players_is_idempotent` — run twice, assert no duplicates and that
  changed fields (e.g. a trade changing `team`) are updated.
- `test_search_rank_sentinels_normalised` — `999` and `9999999` both become `None`.
- `test_rookie_year_parsed_from_metadata` — string `"2026"` → int `2026`.
- `test_include_inactive_flag` — everything in the fixture is stored.
- `test_sync_run_records_failure` — client raises; `SyncRun.status == "failed"`
  and the error is captured.
- `apps/sleeper/tests/test_client.py` — retry/timeout config and `SleeperAPIError`
  on a 500, with `requests` mocked. **No test hits the network.**
- Manual: `make sync-players` then `python manage.py shell` — confirm
  `Player.objects.count()` ≈ 1043, `values("team").distinct().count() == 32`, and
  `Player.objects.filter(full_name="Tom Brady").exists()` is `False`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
