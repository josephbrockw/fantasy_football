# 02 — Draft-capital loader, sync & command

Feature: `011_external-data-enrichment`

## Objective

Download the DynastyProcess `db_playerids.csv` release, crosswalk each row to a
`Player` by **`sleeper_id`**, and populate `PlayerProfile` draft capital
(`draft_year` / `draft_round` / `draft_pick` / `draft_team`) plus the crosswalk
ids (`pfr_id`, `gsis_id`). Ships a file loader/client (its own module, **not**
`SleeperClient`), a `sync_profiles` service, a `sync_profiles` management command
and a `make sync-profiles` target. Writes are idempotent bulk upserts wrapped in
a `SyncRun`; rows that don't map to a known `Player` are **skipped and counted**.

## Scope

**In scope**
- `apps/enrichment/loaders.py` — an HTTP CSV loader + a `ProfileSource` protocol
- `apps/enrichment/services.py` — `sync_profiles` + row builder + bulk upsert +
  `sleeper_id` crosswalk + skip/count + `SyncRun` integration
- `apps/enrichment/management/commands/sync_profiles.py`
- `make sync-profiles` target in the `Makefile`
- A tiny CSV fixture + a `FakeProfileLoader` for tests

**Out of scope**
- The `PlayerProfile` model / migration / admin (PR 01)
- Athleticism / combine columns — those stay null here; PR 03 fills them
- Creating `Player` rows for unmatched ids — unmatched rows are **skipped**, not
  inserted (see rationale below)
- Any player-facing view

## Design decision: skip rows with no matching Player (the crosswalk)

`db_playerids` is keyed by the entire football-database universe (thousands of
players across many id systems, including college and long-retired names), while
our `Player` table deliberately holds only the ~1,043 live players plus anyone
rostered in a tracked league. The crosswalk is the crux of this feature:

- The file carries a **`sleeper_id`** column — the only id that maps to our
  `Player.sleeper_id`. Rows with an empty `sleeper_id`, or a `sleeper_id` we
  don't track, **cannot** join and are **skipped + counted** — exactly the
  "resolve known ids, skip the rest" pattern in `sync_stats` / `sync_trending`
  (`ingest_week`) and `sync_trending`. Resolve the known set with **one**
  `Player.objects.filter(sleeper_id__in=...)` query rather than per-row lookups.
- This keeps the profile table bounded to tracked players and means a source
  keyed by the whole universe never fails on a missing FK. (Unlike the league
  sync's `ensure_players_exist`, there's no FK-integrity forcing function here —
  a skipped row simply isn't stored.)

## Design decision: a file loader, not the Sleeper client

The source is a **static release file over HTTP**, not the Sleeper API, so it
gets its **own** thin loader in `apps/enrichment/loaders.py` — do **not** route
it through `SleeperClient`. It keeps the same discipline as `SleeperClient`: a
narrow capability `Protocol` so the service depends on an interface (and tests
pass a fake), a `requests.Session` with a retry adapter, a bounded timeout, and a
single error type. It downloads a CSV and parses it with the **stdlib `csv`
module** (`csv.DictReader`) — no `pandas`/`pyarrow` dependency.

> **Confirm the URL in implementation.** Do not trust a stale link. Verify the
> current DynastyProcess `db_playerids.csv` raw release URL (the
> `dynastyprocess/data` GitHub repo publishes `files/db_playerids.csv`) at build
> time and set it as the module default `DB_PLAYERIDS_URL`; expose a `--url`
> flag so a specific release can be pinned. Note the columns actually present
> (`sleeper_id`, `draft_year`, `draft_round`, `draft_ovr`/`draft_pick`,
> `draft_team`, `pfr_id`, `gsis_id`) and adjust the field mapping to the real
> header names.

## Implementation plan

1. **Loader + protocol** in `apps/enrichment/loaders.py`:

   ```python
   DB_PLAYERIDS_URL = "https://.../dynastyprocess/data/.../db_playerids.csv"  # confirm
   DEFAULT_TIMEOUT = (5.0, 60.0)
   USER_AGENT = "dynasty-hq/0.1 (+personal fantasy football tooling)"

   class ProfileLoadError(RuntimeError):
       """A release download failed, returned a non-200, or wasn't parseable CSV."""

   class ProfileSource(Protocol):
       """What sync_profiles needs — a list of raw id/draft rows."""
       def fetch_player_ids(self) -> list[dict[str, str]]: ...

   class DynastyProcessLoader:
       """Downloads db_playerids.csv and returns it as a list of dict rows."""
       def __init__(self, url: str = DB_PLAYERIDS_URL, session=None, timeout=DEFAULT_TIMEOUT): ...
       def fetch_player_ids(self) -> list[dict[str, str]]:
           text = self._download(self.url)          # GET, raise ProfileLoadError on failure
           return list(csv.DictReader(io.StringIO(text)))
   ```
   Build the `requests.Session` with a `Retry` adapter and the `User-Agent`, the
   same shape as `SleeperClient.build_session()`. `_download` GETs the URL,
   raises `ProfileLoadError` on a `RequestException` or non-200, and returns
   `response.text`. Keep it network-free in tests via the protocol.

2. **Coercion + row builder** in `apps/enrichment/services.py`. Add small,
   defensive coercers (a private `_as_int`/`_as_str` mirroring
   `apps/players/services.py`; CSV values are all strings and blanks are `""`).
   A builder that turns one raw CSV row + its resolved `Player` into an unsaved
   `PlayerProfile`, setting `updated_at = timezone.now()` so the bulk upsert
   refreshes it (the `auto_now` bypass caveat):

   ```python
   def profile_from_row(player: Player, row: dict[str, str]) -> PlayerProfile:
       return PlayerProfile(
           player=player,
           draft_year=_as_int(row.get("draft_year")),
           draft_round=_as_int(row.get("draft_round")),
           draft_pick=_as_int(row.get("draft_ovr") or row.get("draft_pick")),
           draft_team=_as_str(row.get("draft_team")),
           pfr_id=_as_str(row.get("pfr_id")),
           gsis_id=_as_str(row.get("gsis_id")),
           source="db_playerids",
           updated_at=timezone.now(),
       )
   ```
   Only draft/id fields are written here; the combine measurables stay null (PR
   03 fills them, so they are **not** in this PR's `update_fields`).

3. **Bulk upsert** in `apps/enrichment/services.py`, mirroring `upsert_players`
   / `upsert_week_stats`:

   ```python
   PROFILE_DRAFT_UPDATE_FIELDS = [
       "draft_year", "draft_round", "draft_pick", "draft_team",
       "pfr_id", "gsis_id", "source", "updated_at",
   ]

   def upsert_profiles(rows: list[PlayerProfile]) -> int:
       if not rows:
           return 0
       PlayerProfile.objects.bulk_create(
           rows,
           batch_size=BATCH_SIZE,
           update_conflicts=True,
           unique_fields=["player"],
           update_fields=PROFILE_DRAFT_UPDATE_FIELDS,
       )
       return len(rows)
   ```
   `unique_fields=["player"]` is the OneToOne key from PR 01, so a re-run
   refreshes in place. Restricting `update_fields` to draft/id columns means a
   later `db_playerids` refresh never clobbers combine data PR 03 wrote.

4. **`sync_profiles` service** — the orchestrator:

   ```python
   def sync_profiles(
       loader: ProfileSource | None = None,
       *,
       dry_run: bool = False,
   ) -> SyncStats:
   ```
   - `loader = loader or DynastyProcessLoader()`.
   - Reuse (or re-declare) a small `SyncStats(written, skipped)` dataclass like
     `apps/players/services.py`.
   - Wrap in `with SyncRun.track(SyncRun.Kind.PROFILES) as run:`.
   - `rows = loader.fetch_player_ids()`. Build a `{sleeper_id: player}` map with
     **one** query over the `sleeper_id`s present in the file
     (`Player.objects.filter(sleeper_id__in=ids)`). Iterate rows: an empty or
     unknown `sleeper_id` increments `skipped`; a matched row appends a
     `profile_from_row`. `dry_run` builds but doesn't upsert (parity with
     `sync_players`). Set `run.records_written` / `run.records_skipped`.
   - A `BATCH_SIZE = 500` module constant, matching the players service.

5. **Management command** in
   `apps/enrichment/management/commands/sync_profiles.py`, modelled on
   `sync_stats`:
   - `--url` (override the release URL → build a `DynastyProcessLoader(url=...)`),
   - `--dry-run`.
   - Catch `ProfileLoadError` → `CommandError`, and print
     `f"Wrote {written} profile(s); skipped {skipped} unmatched."` via
     `self.style.SUCCESS`.

6. **Makefile** — add a `sync-profiles` target next to `sync-stats`, and add
   `sync-profiles` to the `.PHONY` list at the top:

   ```make
   sync-profiles:  ## Enrich players with external draft capital & ids
   	$(EXEC) python manage.py sync_profiles $(ARGS)
   ```

7. **Fixtures + fake** — add `apps/enrichment/tests/fixtures/db_playerids_sample.csv`,
   a hand-trimmed CSV with the real header row and a handful of records: two ids
   whose `sleeper_id` exists in `apps/players/tests/fixtures/players_sample.json`
   (the match case — e.g. Chase `7564`, Love `13287`) each with real
   draft_year/round/ovr/team/pfr_id, one row with a `sleeper_id` we **don't**
   track (the skip case), and one row with an **empty** `sleeper_id` (also
   skipped). Add a `FakeProfileLoader` (in `apps/enrichment/tests/utils.py`) that
   reads the CSV fixture via `csv.DictReader`, records calls, honours an injected
   error, and never touches the network — the same discipline as
   `FakeSleeperClient`.

## Testing

In `apps/enrichment/tests/test_services.py` and `test_commands.py` (create
`Player` rows from the shared players fixture / a small helper):

- `test_sync_profiles_writes_matched_skips_unmatched` — matched ids land as
  `PlayerProfile` rows with `draft_year`/`draft_round`/`draft_pick`/`pfr_id`
  populated; the untracked-`sleeper_id` row and the empty-`sleeper_id` row are
  counted in `skipped` and not stored (the crosswalk regression guard).
- `test_sync_profiles_is_idempotent` — run twice: no duplicate rows; mutate a
  draft value in the fixture between runs and assert it updates in place and
  `updated_at` advanced (proves the explicit-`auto_now` workaround).
- `test_sync_profiles_records_run` — a `SyncRun(kind="profiles")` is created with
  `status="success"` and the written/skipped tallies.
- `test_sync_profiles_records_failure` — loader raises `ProfileLoadError`;
  `SyncRun.status == "failed"`, error captured, no partial success.
- `test_dry_run_writes_nothing`.
- `test_draft_only_update_fields` — pre-seed a profile with a combine value
  (`forty=4.4`) set, run `sync_profiles`, assert `forty` is **untouched** while
  draft fields refresh (guards the restricted `update_fields`).
- Loader unit test (`test_loaders.py`) with `requests` mocked: a 200 CSV body is
  parsed into dict rows; a non-200 / `RequestException` raises `ProfileLoadError`.
  **No test hits the network.**
- Command tests: `test_sync_profiles_command` (via `call_command` with a
  `FakeProfileLoader` — inject through the command by patching the loader
  default, or pass `--url` and mock the session), asserting the success line and
  rows written; `test_command_wraps_load_error` (`ProfileLoadError` →
  `CommandError`).
- Manual: `make sync-profiles ARGS="--dry-run"` then, after a real run, a shell
  check that `PlayerProfile.objects.exclude(draft_year=None).exists()`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
