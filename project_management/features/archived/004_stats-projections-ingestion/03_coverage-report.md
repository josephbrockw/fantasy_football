# 03 — Backfill coverage report

Feature: `004_stats-projections-ingestion`

## Objective

A small, read-only `stats_coverage` management command that prints what the
backfill has actually stored — per season, which weeks have stat and projection
rows and their counts — so a full historical backfill can be verified at a glance
without hand-writing shell aggregations. Pure verification aid; no schema or sync
changes.

## Scope

**In scope**
- `apps/players/management/commands/stats_coverage.py`
- Tests for it

**Out of scope**
- Any change to the `PlayerWeekStat` model, the client, or the sync service
- A web/HTMX view (a `/stats/` screen could follow later; this PR keeps the
  verification surface to a command, in keeping with the sync-focused feature)
- Backfilling or mutating any data — this command only reads

## Implementation plan

1. **Command** in `apps/players/management/commands/stats_coverage.py`,
   modelled on the other management commands' structure (`BaseCommand`,
   `add_arguments`, `handle`). Options:
   - `--season` (repeatable / comma-list) to restrict the report to given
     seasons; default all seasons present.
   - `--season-type` (default `regular`).
2. **Aggregate with the ORM, not Python loops over rows.** Use a single grouped
   query, e.g.
   `PlayerWeekStat.objects.values("season", "week", "kind").annotate(
   n=models.Count("id")).order_by("season", "week", "kind")`, filtered by the
   options. Fold the result into a per-season structure keyed by week and kind.
3. **Print a compact grid** to `self.stdout`: one block per season, a line per
   week showing the stat-row and projection-row counts (e.g.
   `2024  W01  stat=1012  proj=1043`), and a season summary line (weeks covered,
   total rows). If nothing is stored, print a clear
   `self.style.WARNING("No PlayerWeekStat rows found — run make sync-stats.")`.
   Keep formatting simple and greppable; this is a diagnostic, not a report page.
4. **No `SyncRun`** — this command reads only and writes nothing, so it does not
   open an audit run.

## Testing

In `apps/players/tests/test_commands.py` (or a new `test_stats_coverage.py`):

- `test_stats_coverage_reports_counts` — create a handful of `PlayerWeekStat`
  rows across two seasons/weeks and both kinds; `call_command("stats_coverage")`
  and assert the captured stdout contains the seasons, weeks, and the correct
  `stat=`/`proj=` counts.
- `test_stats_coverage_season_filter` — `--season 2024` excludes other seasons
  from the output.
- `test_stats_coverage_empty` — with no rows, the warning line is printed and the
  command exits cleanly (no exception).

Run narrowed: `make test ARGS="apps.players"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
