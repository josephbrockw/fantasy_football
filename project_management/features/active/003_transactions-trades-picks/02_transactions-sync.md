# 02 — Transactions & traded-picks sync

Feature: `003_transactions-trades-picks`

## Objective

Pull completed trades and current pick ownership from Sleeper into the PR 01
models: two new client methods, a sync service that parses **only**
`type == "trade"` transactions plus the `/traded_picks` snapshot, a `SyncRun`
audit record, a `sync_transactions` management command, and a `make
sync-transactions` target.

## Scope

**In scope**
- `apps/sleeper/client.py` — `get_league_transactions(league_id, week)` and
  `get_traded_picks(league_id)`, plus a `TransactionSource` protocol
- `apps/sleeper/models.py` — a `SyncRun.Kind.TRANSACTIONS` choice (+ migration)
- `apps/leagues/transactions.py` — a new service module: `sync_transactions(...)`
- `apps/leagues/management/commands/sync_transactions.py`
- `Makefile` — a `sync-transactions` target
- `apps/leagues/tests/factories.py` — trade/pick payload builders + fake-client
  methods
- Tests for the client methods and the sync

**Out of scope**
- Any non-trade transaction type — waiver, free_agent, and commissioner
  transactions are read from the same endpoint and deliberately skipped
- Views/templates (PR 03)
- Backfilling players that are traded but absent from `Player` — a traded player
  is (or recently was) rostered, so `ensure_players_exist` already covered them
  during `make sync-league`; if a referenced `player_id` is somehow missing, log
  and skip that one asset rather than backfilling here

## Implementation plan

### Client (`apps/sleeper/client.py`)

1. Add two methods next to `get_league_rosters`, reusing `_get(..., missing_ok=True)`
   so a purged/unknown league yields `[]` rather than raising — the same 404
   swallow `get_league_rosters` documents:
   ```python
   def get_league_transactions(self, league_id: str, week: int) -> list[dict[str, Any]]:
       return self._get(f"league/{league_id}/transactions/{week}", missing_ok=True) or []

   def get_traded_picks(self, league_id: str) -> list[dict[str, Any]]:
       return self._get(f"league/{league_id}/traded_picks", missing_ok=True) or []
   ```
2. Add a `TransactionSource(Protocol)` declaring just these two methods (the
   split-protocol convention already in the file — each sync depends only on what
   it calls), and add it to the `SleeperAPI` composite protocol so `SleeperClient`
   still satisfies everything.

### SyncRun kind (`apps/sleeper/models.py`)

3. Add `TRANSACTIONS = "transactions", "Transactions"` to `SyncRun.Kind`. This is
   a `TextChoices` change on a `CharField`, so
   `make makemigrations ARGS="sleeper --name add_transactions_synckind"` produces
   a trivial choices-only migration. The dashboard's
   sync-freshness list iterates `SyncRun.Kind.choices`, so the new kind shows up
   there for free.

### Sync service (`apps/leagues/transactions.py`)

Keep this out of the already-long `services.py`; import the shared helpers
(`normalize_league_name` etc.) if needed. The sync operates over **already-synced**
`LeagueSeason` rows — it needs each season's `Team` set to map `roster_id`s to
managers, so it depends on a prior `make sync-league` (document that ordering in
the command help).

4. **`WEEKS` range.** Trades carry a `leg` (week). Loop weeks `1..MAX_WEEK`
   (`MAX_WEEK = 18`, a module constant — regular season plus playoffs; offseason
   trades land on the earliest legs). A per-week 404/`[]` just means "no
   transactions that week", so the loop tolerates empties.

5. **`sync_transactions(client=None, *, season="")`** — the public entrypoint,
   mirroring `sync_leagues`:
   - `client = client or SleeperClient()`.
   - Wrap the body in `with SyncRun.track(SyncRun.Kind.TRANSACTIONS) as run:` and
     set `run.records_written` from a stats object at the end (SyncRun bookkeeping
     stays *outside* the DB transaction, exactly as `sync_leagues` does).
   - Select the seasons to sync: `LeagueSeason.objects.all()` when `season` is
     blank, else `.filter(season=season)`. `select_related("league")`.
   - For each `LeagueSeason`, call `_sync_season_transactions` and
     `_sync_season_traded_picks` inside a `@transaction.atomic` per season (so one
     malformed season can't roll back the others — a per-season atomic block, not
     one giant transaction).

6. **Roster→Team map.** For a `LeagueSeason`, build
   `teams_by_roster = {t.roster_id: t for t in season.teams.select_related("manager")}`
   once, and reuse it for both trades and picks. `Team.manager` gives the
   `Manager` for the pick owners.

7. **`_sync_season_transactions(client, season, teams_by_roster, stats)`**:
   - For `week in range(1, MAX_WEEK + 1)`: `client.get_league_transactions(
     season.sleeper_league_id, week)`.
   - **Filter to trades only:** `if txn.get("type") != "trade": continue`. Also
     skip anything whose `status` is not `"complete"` (dropped/vetoed trade
     offers show up as `type == "trade"` with a non-complete status).
   - Upsert the `Trade` on `sleeper_transaction_id`
     (`Trade.objects.update_or_create`), setting `league_season`, `week=leg`,
     `status`, and `status_updated` — convert Sleeper's epoch-**ms**
     `status_updated` via
     `datetime.fromtimestamp(ms / 1000, tz=timezone.utc)` (guard `None`).
   - Rebuild that trade's assets (`trade.assets.all().delete()` then bulk build),
     for the same reason rosters are rebuilt — cheap, and can't leave stale rows:
     - **Players.** `adds` is `{player_id: roster_id}` (the receiver) and `drops`
       is `{player_id: roster_id}` (the sender). Join on `player_id`: `to_team =
       teams_by_roster.get(adds[pid])`, `from_team = teams_by_roster.get(drops[pid])`.
       Resolve `Player` by `sleeper_id`; if absent, skip that asset (see Out of
       scope) and bump `stats.skipped`. Emit a `TradeAsset(kind=PLAYER, ...)`.
     - **Picks.** Each entry in `draft_picks` has `season`, `round`, `roster_id`
       (original owner — informational here), `previous_owner_id` (sender roster)
       and `owner_id` (receiver roster). Emit `TradeAsset(kind=PICK,
       pick_season=season, pick_round=round, from_team=teams_by_roster.get(
       previous_owner_id), to_team=teams_by_roster.get(owner_id))`.
     - **FAAB.** Each entry in `waiver_budget` has `sender`, `receiver`, `amount`
       (roster ids). Emit `TradeAsset(kind=FAAB, faab_amount=amount,
       from_team=teams_by_roster.get(sender), to_team=teams_by_roster.get(receiver))`.
     - `TradeAsset.objects.bulk_create(assets)`; `stats.trades += 1`,
       `stats.assets += len(assets)`.

8. **`_sync_season_traded_picks(client, season, teams_by_roster, stats)`**:
   - `picks = client.get_traded_picks(season.sleeper_league_id)`.
   - **Rebuild wholesale:** `TradedPick.objects.filter(league_season=season).delete()`
     then bulk-create — current-state, mirroring the roster rebuild.
   - For each pick, map `roster_id → Team.manager` for `original_owner` and
     `owner_id → Team.manager` for `current_owner`. If a `roster_id` has no
     `Team` (an orphan/renumbered roster), skip that pick and bump `stats.skipped`
     — better a missing pick row than a wrong owner.
   - Build `TradedPick(league_season=season, season=pick["season"],
     round=pick["round"], original_owner=..., current_owner=...)`. Guard
     `manager is None` (orphan roster) by skipping. `stats.picks += 1`.
   - **Note:** Sleeper's `/traded_picks` only lists picks that have *changed
     hands*; a pick a manager still owns outright is absent. That's the correct
     semantics for the view ("picks that moved"), so don't synthesise the
     untraded ones.

9. A `@dataclass TransactionSyncStats` with `trades`, `assets`, `picks`,
   `skipped`, and a `total_records` property (`trades + assets + picks`), matching
   `LeagueSyncStats`.

### Management command (`apps/leagues/management/commands/sync_transactions.py`)

10. Model it on `sync_league.py`:
    - `--season` (default `""` → all synced seasons).
    - `help` text noting it requires a prior `make sync-league` (it maps
      `roster_id`s through synced `Team`s).
    - Call `sync_transactions(season=options["season"])`; translate
      `SleeperAPIError` into `CommandError`.
    - On success print a summary (`Synced N trade(s), M asset(s), P traded
      pick(s)`); if `stats.skipped`, note how many assets/picks were skipped for a
      missing player/roster.

### Makefile

11. Add a target mirroring `sync-league`:
    ```make
    sync-transactions:  ## Sync trades and traded draft picks
    	$(EXEC) python manage.py sync_transactions $(ARGS)
    ```
    and add `sync-transactions` to the `.PHONY` list.

## Testing

Extend `apps/leagues/tests/factories.py` and add sync tests. All Sleeper HTTP is
faked; no test touches the network.

- **Factory additions:** `make_trade(...)` (builds a `type == "trade"` payload
  with `adds`/`drops`/`draft_picks`/`waiver_budget`/`leg`/`status`/`status_updated`),
  a `make_non_trade(...)` (e.g. `type == "waiver"`) helper, and a
  `make_traded_pick(...)`. Extend `FakeLeagueClient` (or a thin `FakeTradeClient`
  subclass) with `get_league_transactions(league_id, week)` returning a
  `{(league_id, week): [...]}` map and `get_traded_picks(league_id)` returning a
  `{league_id: [...]}` map, both recording calls like the existing methods.

Client tests in `apps/sleeper/tests/test_league_endpoints.py` (same
`SimpleTestCase` + `fake_response` pattern):
- `test_get_league_transactions` — hits `league/<id>/transactions/<week>` and
  returns the list.
- `test_get_traded_picks` — hits `league/<id>/traded_picks`.
- `test_transaction_endpoints_return_empty_on_404` — a 404 (purged league) →
  `[]` for both, not a raise.

Sync tests in `apps/leagues/tests/test_transactions.py` (`TestCase`, seed a
`LeagueSeason` with two `Team`s + `Manager`s via the factories, then run
`sync_transactions(client=fake)`):
- `test_only_trades_ingested` — a payload mixing a `trade` with a `waiver` and a
  `free_agent` stores exactly one `Trade`; the others are ignored.
- `test_non_complete_trade_skipped` — a `type == "trade"` with a non-`complete`
  status is not stored.
- `test_trade_player_assets` — `adds`/`drops` produce `PLAYER` assets with the
  correct `from_team`/`to_team` (giver vs receiver).
- `test_trade_pick_assets` — a `draft_picks` entry produces a `PICK` asset with
  `pick_season`/`pick_round` and the sender/receiver teams.
- `test_trade_faab_assets` — a `waiver_budget` entry produces a `FAAB` asset with
  the amount and direction.
- `test_missing_traded_player_skipped` — an `adds` referencing an unknown
  `player_id` skips that asset (and bumps `skipped`) without failing the run.
- `test_traded_picks_rebuilt` — picks map to `Manager` owners; a second run with
  a changed `owner_id` reflects the new current owner and leaves no duplicates
  (unique_together holds).
- `test_traded_pick_future_season` — a pick for a season with no `LeagueSeason`
  row still stores with `Manager` owners.
- `test_trade_upsert_idempotent` — running the sync twice yields the same one
  `Trade` and the same asset count (rebuild, no dupes).
- `test_syncrun_recorded` — a `SyncRun(kind="transactions", status="success")`
  row exists with `records_written > 0`; and a raising fake client leaves a
  `failed` run.
- `test_orphan_roster_pick_skipped` — a pick whose `roster_id` has no `Team` is
  skipped rather than raising.

Command test in `apps/leagues/tests/test_commands.py`:
- `test_sync_transactions_command` — patches the service (or injects the fake
  client) and asserts the summary line + a non-zero exit on `SleeperAPIError`.

- Manual: `make up`, `make sync-league`, then `make sync-transactions`; confirm
  in `/admin` that trades, their assets, and `TradedPick` rows are present, and
  that the dashboard's sync-freshness list shows a "Transactions" entry.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
