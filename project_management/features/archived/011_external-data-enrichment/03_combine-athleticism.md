# 03 — Combine athleticism enrichment

Feature: `011_external-data-enrichment`

## Objective

Add athleticism/combine measurables to the `PlayerProfile` rows PR 02 created,
from the nflverse **combine** CSV release. The combine file is keyed by
`pfr_id`, so it joins onto our rows via the `pfr_id` PR 02 captured from
`db_playerids` — no second Sleeper crosswalk needed. Extends the same loader and
`sync_profiles` service rather than adding a parallel pipeline; unmatched combine
rows are skipped and counted.

## Scope

**In scope**
- Extend `apps/enrichment/loaders.py` — a `fetch_combine()` method + the combine
  release URL, added to the `ProfileSource` protocol
- Extend `apps/enrichment/services.py` — a combine pass that updates existing
  `PlayerProfile` rows' measurable columns, joined by `pfr_id`
- A `--combine` / `--source` selection on the `sync_profiles` command (or a
  second sub-step run in the same command by default)
- A tiny combine CSV fixture + `FakeProfileLoader.fetch_combine`

**Out of scope**
- Draft-capital ingest (PR 02) and the model/migration (PR 01)
- Any measurable Sleeper already carries well enough (we don't re-derive age)
- Creating `PlayerProfile` rows for players PR 02 didn't already match — the
  combine pass only **updates** existing rows (a combine-only player with no
  Sleeper id isn't a tracked asset)
- Any player-facing view

## Design decision: join via `pfr_id`, update-only

The nflverse combine release has no `sleeper_id`; its stable id is `pfr_id`
(Pro-Football-Reference), which PR 02 already stored on `PlayerProfile`. So the
combine pass:

- Loads combine rows (`csv.DictReader`), builds a `{pfr_id: row}` from the file.
- Resolves our side with **one** query:
  `PlayerProfile.objects.exclude(pfr_id="").in_bulk(field_name="pfr_id")`
  (or `filter(pfr_id__in=...)`), giving `{pfr_id: profile}` for tracked players
  that carry a `pfr_id`.
- For each matched `pfr_id`, updates the measurable columns on the existing
  profile; combine rows with no matching tracked profile are **skipped +
  counted**. This is the same "resolve known ids, skip the rest" discipline, keyed
  on `pfr_id` this time instead of `sleeper_id`.
- It **only** writes the measurable columns (`update_fields` restricted to the
  combine set), so it never disturbs the draft capital PR 02 wrote — the mirror
  of PR 02 restricting its own `update_fields` to draft/id columns.

> **Confirm the release in implementation.** Verify the current nflverse combine
> CSV URL (the `nflverse/nflverse-data` releases publish a `combine` asset; a
> stable CSV mirror exists under the nflverse data repos) and the real header
> names (`ht`, `wt`, `forty`, `bench`, `vertical`, `broad_jump`, `cone`,
> `shuttle`, `pfr_id`). nflverse height (`ht`) is often a `feet-inches` string —
> normalise it to inches in a coercion helper; `wt` is pounds. Compute `bmi` from
> height/weight when both are present.

## Implementation plan

1. **Loader** — add to `apps/enrichment/loaders.py`:
   - `NFLVERSE_COMBINE_URL` module default (confirm the URL).
   - `def fetch_combine(self) -> list[dict[str, str]]` on `DynastyProcessLoader`
     (or rename the class to a neutral `NflverseLoader` if it now spans both
     ecosystems — a small, reviewable rename; update PR 02's references and
     tests). Add `fetch_combine` to the `ProfileSource` protocol.
   - Accept a per-source URL so a combine release can be pinned independently.

2. **Coercion** in `apps/enrichment/services.py`:
   - `_height_to_inches(value)` — parse `"6-2"`/`"6'2"`/`"74"` → `74` (int) or
     `None`.
   - `_as_float` for `forty`/`vertical`/`cone`/`shuttle`; `_as_int` for
     `weight_lbs`/`bench`/`broad_jump`.
   - `_bmi(height_in, weight_lb)` → `round(weight_lb / height_in**2 * 703, 1)` or
     `None` when either is missing.

3. **Combine upsert path**:

   ```python
   PROFILE_COMBINE_UPDATE_FIELDS = [
       "height_inches", "weight_lbs", "bmi", "forty", "bench",
       "vertical", "broad_jump", "cone", "shuttle", "updated_at",
   ]

   def apply_combine(rows: list[dict[str, str]]) -> tuple[int, int]:
       by_pfr = {r["pfr_id"]: r for r in rows if r.get("pfr_id")}
       known = {
           p.pfr_id: p
           for p in PlayerProfile.objects.filter(pfr_id__in=by_pfr)
       }
       updates, skipped = [], 0
       for pfr_id, row in by_pfr.items():
           profile = known.get(pfr_id)
           if profile is None:
               skipped += 1
               continue
           # set measurable fields + updated_at on the existing instance
           ...
           updates.append(profile)
       PlayerProfile.objects.bulk_update(updates, PROFILE_COMBINE_UPDATE_FIELDS[:-1], batch_size=BATCH_SIZE)
       # (or bulk_create(update_conflicts=True, unique_fields=["player"], update_fields=PROFILE_COMBINE_UPDATE_FIELDS))
       return len(updates), skipped
   ```
   Prefer whichever write matches PR 02's style; if using `bulk_create` upsert,
   set `updated_at` explicitly as before. `bulk_update` is fine here because
   every target row already exists (combine only updates).

4. **Wire into `sync_profiles`** — run the combine pass **after** the draft pass
   inside the *same* `SyncRun.track(SyncRun.Kind.PROFILES)` block so one run
   records the combined written/skipped tally. Add a `--source`
   (`ids` | `combine` | `both`, default `both`) flag on the command so a single
   source can be refreshed alone; `both` runs draft then combine. Update the
   success line to mention both counts.

5. **Fixtures + fake** — add
   `apps/enrichment/tests/fixtures/combine_sample.csv`: the real header plus a
   couple of rows whose `pfr_id` matches the `pfr_id` PR 02's fixture rows set on
   their profiles (the join case) and one row with an untracked `pfr_id` (skip
   case), including a `feet-inches` height to exercise the parser. Extend
   `FakeProfileLoader` with `fetch_combine` returning the fixture.

## Testing

In `apps/enrichment/tests/test_services.py` (extend PR 02's suite; seed profiles
via a `sync_profiles` draft pass first so `pfr_id` is populated):

- `test_apply_combine_updates_matched_skips_unmatched` — a profile whose `pfr_id`
  matches gains `forty`/`vertical`/`bench`/… ; the untracked-`pfr_id` row is
  counted in `skipped` and creates no row.
- `test_combine_preserves_draft_capital` — after the combine pass the row still
  has its PR-02 draft fields intact (guards the restricted `update_fields`).
- `test_height_parsing` — `"6-2"` → `74`, `"74"` → `74`, `""` → `None`.
- `test_bmi_computed` — a known height/weight yields the expected BMI; missing
  either → `None`.
- `test_sync_profiles_both_sources` — a `both` run records one `SyncRun` whose
  written/skipped tally covers draft **and** combine.
- `test_combine_source_only` — `--source combine` (or the service kwarg) updates
  measurables without re-running the draft pass.
- Loader unit test for `fetch_combine` with `requests` mocked (200 CSV → rows;
  non-200 → `ProfileLoadError`). **No test hits the network.**
- Command test: `test_sync_profiles_command_source_flag` asserts `--source
  combine` path.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started. With PR 03 merged, verify the
feature's acceptance criteria and run the completion gate before archiving.
</content>
