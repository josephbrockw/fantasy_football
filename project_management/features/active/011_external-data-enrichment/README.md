# 011 — External Data Enrichment

<!--
Location IS the status: this dir under features/active/ means in progress;
moved to features/archived/ when the Definition of Done is fully checked.
-->

> **Build-order note.** Although this is global feature **011**, it is a
> **prerequisite the user intends to build BEFORE feature 005.** The `NNN` is
> only a stable global id, **not** the build order. This feature enriches the
> `Player` universe with data Sleeper does not expose (chiefly **NFL draft
> capital**), and the analytics/valuation arc consumes it *source-agnostically*:
> 005 (analytics), 006 (player dynasty valuation), and 007 (draft-pick
> valuation) all read these fields as inputs. Plan/implement 011 first so those
> features have draft capital and athleticism to lean on.

## Goals

Ingest per-player data Sleeper doesn't provide — primarily **NFL draft capital**
(draft year / round / pick / team) and secondarily **athleticism/combine
measurables** — from a stable, genuinely-free, permissible **file** source, and
store it keyed to our existing `Player` rows. Draft capital is the single
strongest dynasty signal for young and unproven players (a first-round rookie
with zero NFL snaps is not the same asset as an undrafted one). Concretely, it
is the **backbone of 006's `prospect_score`** — the pedigree prior that (with
evidence decay) separates a prospect with real odds to pop from one eating a
roster spot — so **006 PR 02 hard-depends on this feature's PR 02**, and it
also strengthens the 007 pick-valuation baseline. This is pure
ingestion/enrichment — **no ML** — additive on the 001 foundation and touching
no existing view.

## Source & the id-crosswalk (the crux)

- **File releases, not scraping.** We ingest **versioned, auth-free CSV release
  files** from the nflverse / DynastyProcess ecosystem — *not* web scraping.
  Scraping ESPN / Pro-Football-Reference is fragile and ToS-murky and is
  explicitly **out of scope**. The implementer must confirm the current release
  URLs in the PR plan rather than trusting a hard-coded one.
- **DynastyProcess `db_playerids.csv`** is the primary source: it carries a
  **`sleeper_id` column** — exactly the crosswalk we need — alongside
  `draft_year` / `draft_round` / `draft_pick` (overall) and other id columns
  (`pfr_id`, `gsis_id`, …). One file gives us both the join key and the headline
  draft capital.
- **CSV over parquet** deliberately: parsing with the stdlib `csv` module adds no
  `pandas` / `pyarrow` dependency to the image.
- **The crosswalk is the whole game.** Our `Player` is keyed by a unique
  `sleeper_id` (with an auto `id` PK); the external world is keyed by
  gsis/pfr/mfl ids. Ingest **must** map each incoming row to a `Player` via
  `sleeper_id`, and **skip + count** any row that doesn't resolve to a known
  `Player` — mirroring the "resolve known ids, skip the rest" pattern in
  `sync_stats` / `sync_trending`. A source keyed by the whole football universe
  must never fail on a missing FK.

## Acceptance criteria

<!-- Concrete, verifiable outcomes. Each one must be independently checkable. -->

- [ ] A per-player `PlayerProfile` model (OneToOne to `players.Player`,
      `related_name="profile"`) stores **NFL draft capital** (`draft_year`,
      `draft_round`, `draft_pick`, `draft_team`), the external **crosswalk ids**
      needed for downstream joins (at least `pfr_id`, plus `gsis_id`), and
      **nullable athleticism** measurables (height/weight/BMI plus combine
      fields: forty, bench, vertical, broad jump, cone, shuttle). It has
      `unique` on `player`, supporting indexes, a migration, and an admin
      registration.
- [ ] A **new `apps/enrichment` app** houses a **file loader/client** that
      downloads a versioned, auth-free CSV release (DynastyProcess
      `db_playerids`) over HTTP behind a capability `Protocol` — **not** routed
      through `SleeperClient`, and **not** web scraping. The loader is exercised
      in tests by a **fake loader + a tiny CSV fixture**; **no test touches the
      network**.
- [ ] A `sync_profiles` service crosswalks each incoming row to a `Player` by
      `sleeper_id`, **bulk-upserts** `PlayerProfile` idempotently on its natural
      key (`bulk_create(update_conflicts=True)` with `updated_at` set
      explicitly), and **skips + counts** rows that don't map to a known
      `Player`. The whole run is wrapped in a `SyncRun` (new `profiles` kind)
      recording rows written and skipped, and captures failure without leaving a
      half-recorded run.
- [ ] `make sync-profiles` (management command `sync_profiles`) populates draft
      capital and crosswalk ids for known players; re-running is idempotent (no
      duplicates, values refreshed in place). Flags: `--dry-run`, and a
      `--url`/source override for pinning a release.
- [ ] **Athleticism** measurables (nflverse combine) are joined onto the same
      `PlayerProfile` rows via the `pfr_id` captured from `db_playerids`,
      populating the combine columns for players the combine covers and leaving
      them null otherwise.
- [ ] Surfacing is intentionally minimal: **admin is sufficient**; downstream
      005/006/007 consume the fields. (Optionally a small draft round/pick badge
      is deferred — see PR 03 scope.)
- [ ] `make test`, `make coverage`, and `make quality` all pass; new code is
      covered.

## Pull requests

Work these in `#` order. Each links to its detailed plan. **After a PR's
implementation is finished, stop for review before starting the next one.**
Statuses: `Planned` → `In Progress` → `Complete`.

| # | PR | Status | Notes |
|----|----|--------|-------|
| 01 | [PlayerProfile model, enrichment app & migration](01_playerprofile-model.md) | Planned | |
| 02 | [Draft-capital loader, sync & command](02_draft-capital-loader-and-sync.md) | Planned | The `sleeper_id` crosswalk lives here |
| 03 | [Combine athleticism enrichment](03_combine-athleticism.md) | Planned | Joins via `pfr_id` from PR 02 |

## Out of scope (stated explicitly)

- **Web scraping** of ESPN / Pro-Football-Reference or any HTML source — fragile
  and ToS-murky. File releases only.
- **Play-by-play-derived advanced metrics** (target share, snap share): these are
  **derivable in 005** from the existing Sleeper `PlayerWeekStat` data, so they
  belong there — not duplicated here.
- **Community market-value feeds** (FantasyCalc / KeepTradeCut): their APIs are
  unofficial and less reliable. Worth a **separate future feature** (note it, do
  not build it here). 006 already models a small `TrendingPlayer` market nudge.
- Any player-facing view beyond admin (see per-PR scope; a detail badge is
  deferred).

## Definition of Done

The feature is complete only when every box is checked. Then finalize the docs
and move this directory to `features/archived/`.

- [ ] All acceptance criteria verified
- [ ] All new/changed code has test coverage
- [ ] All tests pass (`make test` / `test-runner`)
- [ ] Coverage confirmed (`make coverage` / `coverage-runner`)
- [ ] Code quality confirmed (`make quality` / `quality-runner`)
- [ ] No outstanding build errors
- [ ] Documentation updated
</content>
</invoke>
