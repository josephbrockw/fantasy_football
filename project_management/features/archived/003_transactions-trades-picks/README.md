# 003 — Transactions, Trades & Traded Picks

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

## Goals

Ingest completed **trades** and current **draft-pick ownership** from Sleeper so
the app can see dynasty's second currency. Today a rival's future first-rounder
being in my column (or vice versa) is completely invisible; trade history is
lost the moment it scrolls off Sleeper's feed. This feature records both, keyed
so pick ownership survives Sleeper minting a new `league_id` every season, and
surfaces them in a read view. **Trades and traded picks only** — waiver claims,
free-agent adds/drops, and commissioner moves are explicitly out of scope
(that's the separate "Waiver/FAAB tracking" backlog item); the transactions
endpoint returns all of those, and we parse only `type == "trade"`.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [x] `apps/leagues/models.py` gains `Trade` (a completed trade within a
      `LeagueSeason`: `sleeper_transaction_id` unique, `week`, `status`,
      `status_updated`), `TradeAsset` (one asset moving in a trade — a `Player`
      FK, or a draft pick `season`/`round`, or a FAAB amount — with sending and
      receiving `Team`), and `TradedPick` (current ownership of a future pick:
      `LeagueSeason`, pick `season`, `round`, `original_owner` and
      `current_owner` as `Manager` FKs). All have a migration and are registered
      in the admin.
- [x] `SleeperClient` gains `get_league_transactions(league_id, week)` and
      `get_traded_picks(league_id)`, both following the `get_league_rosters`
      convention (404 / null → `[]`), with client-level test coverage.
- [x] `make sync-transactions` walks weeks `1..N` of each already-synced
      `LeagueSeason`, ingests only `type == "trade"` transactions (waiver /
      free_agent / commissioner transactions are ignored), and records a
      `SyncRun` (`kind == "transactions"`) that reports success/failure and a
      record count.
- [x] Each stored trade records its assets — traded **players**, traded **draft
      picks** (season + round), and **FAAB** — each with the sending and
      receiving `Team`, resolved by mapping the transaction's `roster_id`s to
      `Team`s within that `LeagueSeason`.
- [x] `traded_picks` ownership is stored as current-state (rebuilt wholesale
      each run, like the roster rebuild), with `original_owner` and
      `current_owner` resolved to a `Manager` (the cross-season-stable
      `sleeper_user_id`) so a pick for a future season whose `LeagueSeason` does
      not exist yet is still attributed correctly.
- [x] Re-running `make sync-transactions` is idempotent: trades upsert on
      `sleeper_transaction_id`, pick ownership is rebuilt, no duplicates.
- [x] A read view at `/league/<slug>/trades/` surfaces trade history
      (newest-first, per season, each side showing what each manager received)
      and the current pick-ownership table, linked from the shared league
      sub-nav.
- [x] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered, with Sleeper HTTP faked from fixtures (no test touches the
      network).

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [Trade, TradeAsset & TradedPick models](01_trade-models.md) | Complete | Reviewed and accepted |
| 02 | [Transactions & traded-picks sync](02_transactions-sync.md) | Complete | Reviewed and accepted |
| 03 | [Trades & pick-ownership view](03_trades-view.md) | Complete | Reviewed and accepted |

## Definition of Done

The feature is complete only when every box is checked. Then finalize the docs
and move this directory to `features/archived/`.

- [x] All acceptance criteria verified
- [x] All new/changed code has test coverage
- [x] All tests pass (`make test` / `test-runner`)
- [x] Coverage confirmed (`make coverage` / `coverage-runner`)
- [x] Code quality confirmed (`make quality` / `quality-runner`)
- [x] No outstanding build errors
- [x] Documentation updated
</content>
</invoke>
