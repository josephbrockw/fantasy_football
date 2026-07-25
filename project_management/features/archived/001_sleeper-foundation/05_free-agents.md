# 05 — Free agent board

Feature: `001_sleeper-foundation`

## Objective

Show every player not rostered anywhere in the league, filterable and overlaid
with what the wider Sleeper population is adding and dropping.

## Scope

**In scope**
- Free agent list view for the current `LeagueSeason`
- Position, age, and NFL-team filters; search by name
- Trending add/drop overlay from `/players/nfl/trending/{add,drop}`
- `TrendingPlayer` model + `sync_trending` command

**Out of scope**
- Adding players to a watchlist or target list (backlog feature 002)
- Any write-back to Sleeper — the API is read-only

## Implementation plan

1. **Free agent queryset** — derived, never stored:
   ```python
   rostered = RosterSlot.objects.filter(
       team__league_season=current_season
   ).values("player_id")
   free_agents = Player.objects.exclude(pk__in=rostered)
   ```
   Restrict to positions the league actually rosters, read from
   `LeagueSeason.roster_positions` (so an IDP or superflex league behaves) rather
   than hardcoding. Exclude the `status="Inactive"` rows by default with a toggle
   to include them.
2. **`TrendingPlayer` model** — FK `player`, `kind` (`add`/`drop`), `count`,
   `lookback_hours`, `synced_at`. `unique_together = ("player", "kind")`, replaced
   wholesale on each sync.
3. **`sync_trending` command** — calls the client for both `add` and `drop`
   (`lookback_hours=24`, `limit=100`). The endpoint returns only
   `{player_id, count}`, so skip ids absent from `Player` rather than failing.
4. **View + filters** — `FreeAgentListView` (paginated, 50/page) with HTMX
   swapping the results table on filter change and `hx-trigger="keyup changed
   delay:300ms"` on the search box. Search hits `search_full_name`, which is
   already indexed and pre-normalised by Sleeper.
5. **Ordering** — default to trending-add count descending, then `search_rank`
   ascending as a tiebreak. `search_rank` is *only* a tiebreak: it collides
   heavily (1,436 players share `9999999`) and is not an ADP, so it must never be
   the primary sort or be shown to the user as a rank.
6. **Template** — reuse `_player_row.html` from PR 04, extended with the trending
   count column. Confirms that partial was built without roster-specific coupling.

## Testing

- `test_free_agents_exclude_rostered_players` — a player on any team in the
  current season is absent; an unrostered player is present.
- `test_free_agents_scoped_to_current_season` — a player rostered only in a *past*
  `LeagueSeason` still shows as a free agent now.
- `test_position_filter_respects_league_roster_positions` — a league without `K`
  in `roster_positions` does not list kickers.
- `test_search_by_name` — partial, case-insensitive match works.
- `test_trending_overlay` — counts render, and a player with no trending row shows
  blank rather than erroring.
- `test_sync_trending_skips_unknown_player_ids` — an id absent from `Player` is
  skipped without raising.
- `test_ordering_default` — trending desc, `search_rank` tiebreak, `NULL`s last.
- `test_pagination`.
- Manual: `make sync-trending`, open the free agent board, filter to RB under 25,
  and search for a known name.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review**. This is the last PR
in the feature — after review, run the full verification gate (`test-runner`,
`coverage-runner`, `quality-runner`), then hand off to `pm-updater` to archive.
