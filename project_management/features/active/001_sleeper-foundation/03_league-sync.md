# 03 — League sync with season rollover

Feature: `001_sleeper-foundation`

## Objective

Model the league, its managers, and every roster — in a way that survives Sleeper
minting a brand-new `league_id` for the same dynasty every season.

## Scope

**In scope**
- `apps/leagues/models.py` — `SleeperAccount`, `League`, `LeagueSeason`,
  `Manager`, `Team`, `RosterSlot`
- The `previous_league_id` chain walk plus the league-name fallback
- On-demand player upsert for referential integrity
- `manage.py sync_league`

**Out of scope**
- Transactions, traded picks, drafts (backlog features 003/004)
- Matchups and weekly scoring
- Any view (PR 04)

## Implementation plan

1. **Models.** The two-level split is the whole point: `League` is the permanent
   dynasty, `LeagueSeason` is one Sleeper league per year.
   - `SleeperAccount` — `username`, `sleeper_user_id`, `is_me`. Store the
     `user_id`, because Sleeper usernames are mutable.
   - `League` — `name`, `slug`, `normalized_name` (casefolded, punctuation and
     whitespace stripped; indexed — this is what the fallback matches on).
   - `LeagueSeason` — FK `league`, `season`, `sleeper_league_id` (unique),
     `previous_sleeper_league_id` (nullable, indexed), `status`, `total_rosters`,
     and JSON `roster_positions` / `scoring_settings` / `settings`.
   - `Manager` — `sleeper_user_id` (unique) — **stable across seasons, this is the
     cross-season identity** — plus `username`, `display_name`, `avatar`, `is_me`.
   - `Team` — FK `league_season`, `roster_id` (int), FK `manager` (nullable;
     orphan rosters exist in real leagues), `team_name`, `wins`, `losses`, `ties`,
     `fpts`, `fpts_against`, `waiver_budget_used`.
     `unique_together = ("league_season", "roster_id")`.
     Note in a docstring that `roster_id` is only unique *within* a season and
     must never be used as a cross-season key.
   - `RosterSlot` — FK `team`, FK `player`, `slot` in
     `starter | bench | taxi | ir`. `unique_together = ("team", "player")`.
2. **Client additions** — `get_user(username_or_id)`,
   `get_user_leagues(user_id, sport, season)`, `get_league(league_id)`,
   `get_league_rosters(league_id)`, `get_league_users(league_id)`.
3. **Chain walk** in `apps/leagues/services.py`:
   ```
   resolve username -> user_id                      (cached on SleeperAccount)
   GET /user/<uid>/leagues/nfl/<season>
   for each league:
       chain = [league]
       while chain[-1].previous_league_id:
           chain.append(GET /league/<previous_league_id>)
   ```
   Guard the loop with a `seen` set of ids and a hard depth cap, so a cyclic or
   self-referential `previous_league_id` cannot hang the sync.
4. **Binding a chain to a `League`**, in this precedence order:
   1. an existing `LeagueSeason.sleeper_league_id` matching **any** link in the chain;
   2. **`normalized_name` match** — the fallback for when the chain is broken,
      which is the behaviour requested: same league name ⇒ same league;
   3. otherwise create a new `League`.
   Then upsert one `LeagueSeason` per chain member.
5. **Rosters.** For each `LeagueSeason`, `GET /rosters` and `GET /users`; upsert
   `Manager` by `sleeper_user_id`, `Team` by `(league_season, roster_id)`, and
   rebuild that team's `RosterSlot` rows. Slot derivation: `starters` →
   `starter`, `taxi` → `taxi`, `reserve` → `ir`, remainder of `players` → `bench`.
   Mark the `Manager` matching `SleeperAccount.sleeper_user_id` as `is_me`.
6. **On-demand player upsert — required, not optional.** The PR 02 filter is a
   heuristic about the NFL, not about this league: a rival can roster an unsigned
   veteran or a UDFA whose `team` is `null`, and that player will be absent from
   the `Player` table. Before writing `RosterSlot` rows, collect every referenced
   `player_id`, diff against `Player.objects`, and fetch/insert the missing ones
   (from the cached dump if fresh, else a targeted refetch), **bypassing the
   filter**. Without this, roster sync dies on a missing foreign key.
7. **`sync_league` command** — `--username` (defaults to `settings.SLEEPER_USERNAME`),
   `--season` (defaults to the current season from `get_nfl_state()`), `--dry-run`.
   Wrap the whole thing in a `SyncRun(kind="league")` and a transaction.

## Testing

All Sleeper HTTP mocked from committed fixtures; no test touches the network.

- `test_rollover_via_previous_league_id` — sync a fake 2026 league, then a 2027
  league whose `previous_league_id` points at it. Assert **one** `League`, **two**
  `LeagueSeason`s, and that managers are preserved by `sleeper_user_id`.
- `test_rollover_via_name_fallback` — same two seasons with the chain broken
  (`previous_league_id: null`) but an identical name. Assert they still bind to
  one `League`.
- `test_distinct_names_stay_separate` — two leagues, different names, no chain:
  two `League` rows. Guards the fallback against over-matching.
- `test_normalized_name_matching` — `"The League"` and `"the  league!"` match.
- `test_chain_cycle_is_guarded` — a `previous_league_id` pointing at itself
  terminates instead of looping forever.
- `test_roster_slots_derived_correctly` — starters/taxi/reserve/bench all land in
  the right slot, and re-syncing rebuilds rather than duplicating.
- `test_unknown_player_is_upserted` — a roster references a `player_id` absent
  from `Player` (the `team: null` veteran case); assert the sync succeeds and the
  player is created.
- `test_orphan_roster_without_owner` — `owner_id: null` yields a `Team` with a
  null `manager` rather than an error.
- `test_is_me_flagged` — the manager matching `SleeperAccount` is flagged.
- Manual: set `SLEEPER_USERNAME` in `.env`, run `make sync-league`, and confirm in
  the admin that the league, its seasons, all teams, and their players are present.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
