# 03 — Read-only metrics report

Feature: `005_player-analytics-layer`

## Objective

A small, read-only `metrics_report` management command that prints what the
recompute has stored — per season, the row count and the top players by
`ppg_ppr` — so a recompute can be verified at a glance without hand-writing shell
aggregations. Pure verification aid, in the same spirit as 004's
`stats_coverage`; no schema or service changes. No player-facing web view is
added.

## Scope

**In scope**
- `apps/players/management/commands/metrics_report.py`
- Tests for it

**Out of scope**
- Any change to `PlayerSeasonMetrics`, the recompute service, or the command from
  PR 02
- A web/HTMX view — a `/players/<id>/` metrics panel could follow later; this
  feature keeps the surface to a command and the admin (registered in PR 01)
- Mutating any data — this command only reads

## Implementation plan

1. **Command** in `apps/players/management/commands/metrics_report.py`, modelled
   on `stats_coverage.py` (`BaseCommand`, `add_arguments`, `handle`). Options:
   - `--season` (comma list) to restrict to given seasons; default all present.
   - `--season-type` (default `regular`).
   - `--position` to restrict the leaderboard to one position (e.g. `WR`).
   - `--top` (int, default `10`) — how many players per season to list.
2. **Read with the ORM, ordered, not Python-sorted.** Per season, count rows and
   pull the top-N by `-ppg_ppr` (nulls excluded via `ppg_ppr__isnull=False`),
   `select_related("player")` so the player name renders without extra queries.
   Get the season list from
   `PlayerSeasonMetrics.objects.values_list("season", flat=True).distinct()`,
   filtered by the options.
3. **Print a compact block per season** to `self.stdout`: a season header, the
   row count, then one line per top player
   (`f"  {rank:2d}. {m.player.full_name:24s} {m.position:3s} "
   f"{m.games_played:2d}g  ppg={m.ppg_ppr:.1f}  Δ{m.form_delta_ppr:+.1f}"`),
   guarding the null-format cases. If nothing is stored, print a clear
   `self.style.WARNING("No PlayerSeasonMetrics rows found — run make
   recompute-metrics.")`.
4. **No `SyncRun`** — this command reads only and writes nothing, so it does not
   open an audit run (same as `stats_coverage`).

## Testing

In `apps/players/tests/test_commands.py` (or a new `test_metrics_report.py`):

- `test_metrics_report_lists_top_players` — create a handful of
  `PlayerSeasonMetrics` rows across two seasons with distinct `ppg_ppr`;
  `call_command("metrics_report")` and assert stdout contains the seasons and the
  players in descending `ppg_ppr` order.
- `test_metrics_report_top_limit` — `--top 1` lists only the leader per season.
- `test_metrics_report_position_filter` — `--position WR` excludes other
  positions.
- `test_metrics_report_empty` — with no rows, the warning line is printed and the
  command exits cleanly (no exception).

Run narrowed: `make test ARGS="apps.players"`.

## Review checkpoint

When the steps above are done: confirm tests pass (`make test`) and quality is
clean (`make quality`), then **stop and hand off for review** before this PR is
marked `Complete` or the next PR is started.
</content>
