# 03 — Trades & pick-ownership view

Feature: `003_transactions-trades-picks`

## Objective

Surface the data ingested in PR 02: a read page at `/league/<slug>/trades/`
showing this league's trade history (newest-first, each side showing what each
manager received) and the current traded-pick ownership table, linked from the
shared league sub-nav.

## Scope

**In scope**
- A `TradesView` in `apps/leagues/views.py` + its URL
- Templates `leagues/trades.html` (+ a `_trades_list.html` / `_pick_ownership.html`
  partial if it keeps the page readable)
- A season picker (reusing the `LeagueOverviewView` pattern)
- A "Trades" link in the shared league sub-nav
- View tests

**Out of scope**
- Any write/edit path — this is read-only
- HTMX inline editing (nothing to edit here); a season-picker reload is a plain
  GET, matching `LeagueOverviewView`
- Trade evaluation / pick valuation / ML (future backlog)

## Implementation plan

This view follows the `LeagueOverviewView` shape (a `League` `DetailView` by
slug, with a season picker), not the free-agent `ListView` — trade volume per
season is small, so pagination is unnecessary.

1. **URL** in `apps/leagues/urls.py`, alongside the other `league/<slug>/…`
   routes:
   ```python
   path("league/<slug:slug>/trades/", views.TradesView.as_view(), name="trades"),
   ```

2. **`TradesView(DetailView)`** in `apps/leagues/views.py`:
   - `model = League`, `slug_field = "slug"`, `template_name =
     "leagues/trades.html"`, `context_object_name = "league"`.
   - Reuse the season-picker logic from `LeagueOverviewView.pick_season` (either
     factor it into a small shared mixin/helper or mirror it): `seasons =
     list(league.seasons.all())` (newest-first), pick `?season=` else newest.
   - **Trades context:** for the selected `season`,
     ```python
     Trade.objects.filter(league_season=season)
         .prefetch_related(
             "assets__player",
             "assets__from_team__manager",
             "assets__to_team__manager",
         )
         .order_by("-status_updated")
     ```
     Group each trade's assets **by receiving manager** so the template can render
     "Manager A received: …" / "Manager B received: …". Do this grouping in Python
     in the view (a small `@dataclass TradeSummary` with the trade plus a list of
     `(manager, [assets])` sides), keeping the template logic-light — the same way
     `starting_lineup` prepares `LineupRow`s for the roster template. Use each
     asset's `label` property (added in PR 01) so the template doesn't branch on
     `kind`.
   - **Pick-ownership context:**
     ```python
     TradedPick.objects.filter(league_season=season)
         .select_related("original_owner", "current_owner")
         .order_by("season", "round", "original_owner__display_name")
     ```
     Pass straight through; each row is `{pick season} R{round}` — originally
     `original_owner`, now owned by `current_owner`. (Recall from PR 02 that
     `/traded_picks` only lists picks that *changed hands*, so this table is
     exactly "picks that have moved".)
   - `get_context_data` sets `season`, `seasons`, `trades` (the summaries), and
     `traded_picks`. Degrade gracefully when the league has no synced season
     (`season is None` → empty lists, an empty state), mirroring
     `LeagueOverviewView`.

3. **Templates** under `apps/leagues/templates/leagues/`, extending `base.html`
   and reusing the existing styling:
   - `trades.html` — the season picker (copy the `<select>`/GET form from
     `league_overview.html`); a **Trades** section listing each `TradeSummary` as
     a card (date from `status_updated`, week, and one column per side showing
     that manager and the assets they received — a "mine" marker when
     `to_team.manager.is_me`, consistent with the dashboard/targets treatment); a
     **Pick ownership** section rendering `traded_picks` as a table (Pick / From /
     To); and empty states ("No trades recorded for this season", "No picks have
     changed hands").
   - Reuse `leagues/_player_row.html` (or its player-name snippet) for `PLAYER`
     assets so player rows look identical to the rest of the app; render `PICK`
     and `FAAB` assets from their `label`.
   - Split out `_trades_list.html` / `_pick_ownership.html` partials only if
     `trades.html` gets unwieldy.

4. **Sub-nav.** Add a "Trades" link to the shared league sub-nav introduced in
   feature 002 (overview / free agents / rookies / targets → **trades**). Find
   the sub-nav partial/block those links live in (search the templates for the
   `leagues:free_agents` / `scouting:` links) and add
   `{% url 'leagues:trades' league.slug %}` in the same place, marked active on
   this page.

## Testing

Add `apps/leagues/tests/test_trades_view.py` (`TestCase` + Django test client),
seeding via the factories: a `LeagueSeason`, my `Team` (`Manager.is_me=True`) and
a rival, one `Trade` with a player + a pick + FAAB asset, and a couple of
`TradedPick` rows.

- `test_trades_page_renders` — 200; the trade appears with both managers and
  their received assets.
- `test_asset_kinds_render` — the player name, the `{season} R{round}` pick, and
  the FAAB amount all show.
- `test_mine_marker` — assets my team received are marked "mine".
- `test_season_picker` — `?season=<older>` shows that season's trades; the
  default is the newest season.
- `test_pick_ownership_table` — `TradedPick` rows render with original vs current
  owner.
- `test_empty_states` — a league/season with no trades and no traded picks shows
  both empty states rather than erroring.
- `test_subnav_link_present` — the league sub-nav includes the Trades link.
- `test_query_budget` — an `assertNumQueries` guard on the trades page
  (the `prefetch_related` should keep it flat regardless of trade count).
- Manual: `make up`, sync a league and its transactions, open
  `/league/<slug>/trades/`, switch seasons, and confirm the trade cards and pick
  table read correctly.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review**. This is the last
PR in the feature — after review, run the full verification gate (`test-runner`,
`coverage-runner`, `quality-runner`), then hand off to `pm-updater` to archive.
</content>
