from __future__ import annotations

from django.test import TestCase

from apps.players.models import Player, PlayerSeasonMetrics, PlayerWeekStat
from apps.players.services import metrics_from_week_rows, recompute_metrics
from apps.sleeper.models import SyncRun


def make_player(sleeper_id: str = "p", position: str = "WR") -> Player:
    return Player.objects.create(
        sleeper_id=sleeper_id, full_name="A Player", position=position
    )


def unsaved_week(
    ppr: float | None, week: int = 1, stats: dict | None = None
) -> PlayerWeekStat:
    return PlayerWeekStat(week=week, pts_ppr=ppr, stats=stats or {})


def stat_week(player: Player, season: int, week: int, ppr: float | None) -> None:
    PlayerWeekStat.objects.create(
        player=player,
        season=season,
        week=week,
        kind=PlayerWeekStat.Kind.STAT,
        pts_ppr=ppr,
    )


class MetricsHelperTests(TestCase):
    def test_computes_per_game_and_consistency(self) -> None:
        player = Player(full_name="X", position="WR", sleeper_id="p")
        rows = [unsaved_week(10, 1), unsaved_week(20, 2), unsaved_week(30, 3)]
        m = metrics_from_week_rows(player, 2024, "regular", rows)
        self.assertEqual(m.games_played, 3)
        self.assertEqual(m.total_ppr, 60)
        self.assertEqual(m.ppg_ppr, 20.0)
        assert m.stdev_ppr is not None
        self.assertAlmostEqual(m.stdev_ppr, 8.16496, places=4)
        self.assertEqual(m.floor_ppr, 10)
        self.assertEqual(m.ceiling_ppr, 30)
        self.assertEqual(m.position, "WR")

    def test_recent_form_delta(self) -> None:
        player = Player(full_name="X", position="WR", sleeper_id="p")
        # RECENT_WINDOW=4 → last four of six are [10, 10, 30, 30] → mean 20.
        rows = [unsaved_week(v, i + 1) for i, v in enumerate([10, 10, 10, 10, 30, 30])]
        m = metrics_from_week_rows(player, 2024, "regular", rows)
        assert m.form_delta_ppr is not None and m.ppg_ppr is not None
        self.assertEqual(m.recent_ppg_ppr, 20.0)
        self.assertGreater(m.form_delta_ppr, 0)  # trending up
        self.assertAlmostEqual(m.form_delta_ppr, 20.0 - m.ppg_ppr)

    def test_unplayed_weeks_excluded(self) -> None:
        player = Player(full_name="X", position="WR", sleeper_id="p")
        rows = [unsaved_week(10, 1), unsaved_week(None, 2), unsaved_week(20, 3)]
        m = metrics_from_week_rows(player, 2024, "regular", rows)
        self.assertEqual(m.games_played, 2)
        self.assertEqual(m.ppg_ppr, 15.0)

    def test_zero_played_weeks_yields_nulls(self) -> None:
        player = Player(full_name="X", position="WR", sleeper_id="p")
        m = metrics_from_week_rows(player, 2024, "regular", [unsaved_week(None, 1)])
        self.assertEqual(m.games_played, 0)
        self.assertIsNone(m.ppg_ppr)
        self.assertIsNone(m.stdev_ppr)

    def test_usage_proxies_summed(self) -> None:
        player = Player(full_name="X", position="WR", sleeper_id="p")
        rows = [
            # pts_* is a promoted scoring key (skipped); "note" is non-numeric.
            unsaved_week(
                10, 1, stats={"rec_tgt": 8, "rush_att": 2, "pts_ppr": 10, "note": "x"}
            ),
            unsaved_week(12, 2, stats={"rec_tgt": 10}),
        ]
        m = metrics_from_week_rows(player, 2024, "regular", rows)
        self.assertEqual(m.targets, 18)
        self.assertEqual(m.carries, 2)
        self.assertIsNone(m.snaps)  # off_snp absent everywhere
        self.assertEqual(m.usage["rec_tgt"], 18)
        self.assertNotIn("pts_ppr", m.usage)  # scoring keys excluded
        self.assertNotIn("note", m.usage)  # non-numeric excluded


class RecomputeMetricsTests(TestCase):
    def test_is_idempotent_and_updates_in_place(self) -> None:
        player = make_player()
        stat_week(player, 2024, 1, 10.0)
        stat_week(player, 2024, 2, 20.0)
        recompute_metrics(seasons=[2024])
        first = PlayerSeasonMetrics.objects.get(player=player, season=2024)

        recompute_metrics(seasons=[2024])
        rows = PlayerSeasonMetrics.objects.filter(player=player, season=2024)
        self.assertEqual(rows.count(), 1)
        updated = rows.get()
        self.assertEqual(updated.ppg_ppr, 15.0)
        self.assertGreater(updated.updated_at, first.updated_at)

    def test_uses_only_stat_kind(self) -> None:
        player = make_player()
        stat_week(player, 2024, 1, 10.0)
        PlayerWeekStat.objects.create(
            player=player,
            season=2024,
            week=1,
            kind=PlayerWeekStat.Kind.PROJECTION,
            pts_ppr=99.0,
        )
        recompute_metrics(seasons=[2024])
        m = PlayerSeasonMetrics.objects.get(player=player, season=2024)
        self.assertEqual(m.games_played, 1)  # the projection is ignored
        self.assertEqual(m.ppg_ppr, 10.0)

    def test_wraps_syncrun(self) -> None:
        player = make_player()
        stat_week(player, 2024, 1, 10.0)
        recompute_metrics(seasons=[2024])
        run = SyncRun.objects.get(kind=SyncRun.Kind.METRICS)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertEqual(SyncRun.Kind.METRICS, "metrics")

    def test_dry_run_writes_nothing(self) -> None:
        player = make_player()
        stat_week(player, 2024, 1, 10.0)
        stats = recompute_metrics(seasons=[2024], dry_run=True)
        self.assertEqual(PlayerSeasonMetrics.objects.count(), 0)
        self.assertGreater(stats.written, 0)

    def test_default_recomputes_all_present_seasons(self) -> None:
        player = make_player()
        stat_week(player, 2023, 1, 5.0)
        stat_week(player, 2024, 1, 10.0)
        recompute_metrics()  # no seasons → all present
        self.assertEqual(PlayerSeasonMetrics.objects.filter(player=player).count(), 2)

    def test_season_with_no_stats_writes_nothing(self) -> None:
        stats = recompute_metrics(seasons=[1999])  # no stat rows for 1999
        self.assertEqual(stats.written, 0)
        self.assertFalse(PlayerSeasonMetrics.objects.exists())
