# 01 — Trade, TradeAsset & TradedPick models

Feature: `003_transactions-trades-picks`

## Objective

Add the three models that hold trade history and draft-pick ownership, with a
migration and admin registration. No sync and no view yet — this PR is just the
schema, so it can be reviewed on the modelling decisions alone (especially the
`Team`-vs-`Manager` keying, which is the crux of the feature).

## Scope

**In scope**
- `apps/leagues/models.py` — `Trade`, `TradeAsset`, `TradedPick`
- The migration for them
- Admin registration in `apps/leagues/admin.py`
- Model-level unit tests

**Out of scope**
- The Sleeper client methods and the sync service (PR 02)
- The `SyncRun.Kind.TRANSACTIONS` choice — it is only consumed by the sync, so
  it is added with the sync in PR 02
- Any view or template (PR 03)
- Waiver / free-agent / commissioner transactions (out of the whole feature)

## Implementation plan

All three models live in `apps/leagues/models.py` alongside `Team` / `RosterSlot`
and subclass `apps.core.models.TimeStampedModel` (as the existing league models
do). Keep the docstrings load-bearing — explain *why* the keying is what it is,
matching the tone of the `Team` / `RosterSlot` docstrings already there.

1. **`Trade`** — one completed trade within a `LeagueSeason`.
   - `league_season` — FK → `LeagueSeason`, `on_delete=CASCADE`,
     `related_name="trades"`. A trade is between rosters *within one season*, so
     it hangs off the season, not the permanent `League`.
   - `sleeper_transaction_id` — `CharField(max_length=32, unique=True)`. The
     idempotency key the sync upserts on.
   - `week` — `PositiveIntegerField`. Sleeper's transaction `leg`; the endpoint
     is per-week so this is known at ingest.
   - `status` — `CharField(max_length=16, blank=True)` (Sleeper sends
     `"complete"`; store it verbatim rather than assuming).
   - `status_updated` — `DateTimeField(null=True, blank=True)`. Sleeper's
     `status_updated` is epoch **milliseconds**; the sync converts it to an aware
     datetime (note that in the field help_text/docstring).
   - `Meta`: `ordering = ["-status_updated"]`;
     `indexes = [models.Index(fields=["league_season", "-status_updated"])]`.
   - `__str__` → e.g. `f"Trade {self.sleeper_transaction_id} (wk {self.week})"`.

2. **`TradeAsset`** — one asset moving in a trade. Sleeper spreads a trade across
   three parallel structures (`adds`/`drops` map `player_id → roster_id`,
   `draft_picks` is a list, `waiver_budget` is FAAB), so model the *union* with a
   `kind` discriminator rather than three tables.
   - `trade` — FK → `Trade`, `on_delete=CASCADE`, `related_name="assets"`.
   - `kind` — `TextChoices`: `PLAYER = "player"`, `PICK = "pick"`,
     `FAAB = "faab"`.
   - `player` — FK → `apps.players.models.Player`, `on_delete=PROTECT`,
     `null=True, blank=True`, `related_name="trade_assets"`. Set for `PLAYER`
     assets. (`PROTECT`, not `CASCADE`: deleting a player should never silently
     erase trade history.)
   - `pick_season` — `CharField(max_length=8, blank=True)` and `pick_round` —
     `PositiveIntegerField(null=True, blank=True)`. Set for `PICK` assets.
   - `faab_amount` — `PositiveIntegerField(null=True, blank=True)`. Set for
     `FAAB` assets.
   - `from_team` — FK → `Team`, `on_delete=SET_NULL`, `null=True, blank=True`,
     `related_name="assets_sent"`. The roster giving the asset up.
   - `to_team` — FK → `Team`, `on_delete=SET_NULL`, `null=True, blank=True`,
     `related_name="assets_received"`. The roster receiving it. `Team` already
     carries `manager`, so the sending/receiving *manager* is reachable via
     `from_team.manager` / `to_team.manager` — no need to duplicate it, and
     `Team` is the right grain because a trade is season-scoped.
   - `Meta`: `ordering = ["kind"]`.
   - `__str__` → a compact label per kind (player name, `f"{season} R{round}
     pick"`, or `f"${amount} FAAB"`).
   - Optionally add a `label` property that renders the asset regardless of kind,
     so the PR 03 template does not branch on `kind` in the DTL.

3. **`TradedPick`** — current ownership of a future draft pick. This is
   current-state, not history: it is the snapshot Sleeper returns from
   `/traded_picks`, rebuilt wholesale on each sync (mirroring how `RosterSlot`
   and `TrendingPlayer` are replaced rather than diffed).
   - `league_season` — FK → `LeagueSeason`, `on_delete=CASCADE`,
     `related_name="traded_picks"`. The season whose `/traded_picks` snapshot
     this row came from.
   - `season` — `CharField(max_length=8)`. The **pick's** season, e.g. `"2027"` —
     often a future year with no `LeagueSeason` row yet, which is exactly why the
     owners key on `Manager`, not `Team`.
   - `round` — `PositiveIntegerField`.
   - `original_owner` — FK → `Manager`, `on_delete=CASCADE`,
     `related_name="picks_originally_owned"`. Sleeper identifies this by
     `roster_id`; the sync maps that `roster_id` through the season's `Team` to
     the cross-season-stable `Manager`.
   - `current_owner` — FK → `Manager`, `on_delete=CASCADE`,
     `related_name="picks_owned"`. Same mapping from `owner_id`.
   - `Meta`: `unique_together = ("league_season", "season", "round",
     "original_owner")` — a pick is identified by whose it originally was;
     `ordering = ["season", "round"]`;
     `indexes = [models.Index(fields=["league_season", "season"])]`.
   - `__str__` → e.g. `f"{self.season} R{self.round} pick"`.
   - Note in the docstring that `roster_id` is season-scoped (never a
     cross-season key — the same caveat `Team` already documents), which is why
     ownership persists on `Manager`.

4. **Migration.** `make makemigrations ARGS="leagues --name add_trade_models"`
   (always name migrations; the number is auto-assigned). Review it: three
   `CreateModel`s, the `unique_together` on `TradedPick`, and the indexes.
   `make migrate`.

5. **Admin** in `apps/leagues/admin.py`, following the existing inline style:
   - `TradeAssetInline(admin.TabularInline)` on `Trade` (fields `kind`,
     `player`, `pick_season`, `pick_round`, `faab_amount`, `from_team`,
     `to_team`; `autocomplete_fields = ("player",)`).
   - `@admin.register(Trade)` — `list_display = ("sleeper_transaction_id",
     "league_season", "week", "status", "status_updated")`;
     `list_filter = ("league_season__season", "league_season__league")`;
     `inlines = [TradeAssetInline]`.
   - `@admin.register(TradedPick)` — `list_display = ("season", "round",
     "original_owner", "current_owner", "league_season")`;
     `list_filter = ("season", "league_season__league")`;
     `search_fields = ("original_owner__display_name",
     "current_owner__display_name")`.

## Testing

Add to `apps/leagues/tests/test_models.py` (or a new
`apps/leagues/tests/test_trade_models.py`), building on the existing model-test
patterns:

- `test_trade_str_and_ordering` — two trades on one `LeagueSeason` order
  newest-`status_updated`-first.
- `test_trade_unique_transaction_id` — a second `Trade` with the same
  `sleeper_transaction_id` raises `IntegrityError`.
- `test_asset_label_per_kind` — a `PLAYER`, a `PICK`, and a `FAAB` asset each
  render the expected `label`/`__str__`.
- `test_asset_player_protected` — deleting a `Player` referenced by a
  `TradeAsset` is prevented (`PROTECT`); trade history survives.
- `test_traded_pick_unique_together` — a duplicate
  `(league_season, season, round, original_owner)` raises `IntegrityError`; the
  same pick round for a *different* `original_owner` is allowed.
- `test_traded_pick_owners_are_managers` — `original_owner` / `current_owner`
  resolve to `Manager`s and expose the `picks_owned` / `picks_originally_owned`
  reverse relations.

Use the existing `apps/leagues/tests/factories.py` helpers where they fit
(`Manager`, `Team`, `LeagueSeason` are created directly in the model tests
today); no Sleeper client is involved in this PR.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
