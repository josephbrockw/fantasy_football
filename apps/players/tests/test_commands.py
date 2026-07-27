from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.players.models import (
    Player,
    PlayerSeasonMetrics,
    PlayerValue,
    PlayerWeekStat,
    TrendingPlayer,
)
from apps.players.tests.utils import BRADY, CHASE, FakeSleeperClient
from apps.sleeper.client import SleeperAPIError
from apps.sleeper.models import SyncRun

COMMAND_PATH = "apps.players.services.SleeperClient"


class SyncPlayersCommandTests(TestCase):
    def call(self, *args: str) -> str:
        out = StringIO()
        call_command("sync_players", *args, stdout=out)
        return out.getvalue()

    def test_syncs_and_reports_counts(self) -> None:
        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            output = self.call()

        self.assertIn("Wrote 4 players", output)
        self.assertIn("2 skipped", output)
        self.assertEqual(Player.objects.count(), 4)

    def test_include_inactive_flag(self) -> None:
        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            self.call("--include-inactive")

        self.assertEqual(Player.objects.count(), 6)
        self.assertTrue(Player.objects.filter(sleeper_id=BRADY).exists())

    def test_dry_run_flag(self) -> None:
        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            output = self.call("--dry-run")

        self.assertIn("Would write 4 players", output)
        self.assertEqual(Player.objects.count(), 0)

    def test_skips_when_synced_within_24_hours(self) -> None:
        SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.SUCCESS,
            finished_at=timezone.now() - timedelta(hours=2),
        )
        client = FakeSleeperClient()

        with mock.patch(COMMAND_PATH, return_value=client):
            output = self.call()

        self.assertIn("skipping", output)
        self.assertEqual(client.calls, [])
        self.assertEqual(Player.objects.count(), 0)

    def test_force_overrides_the_throttle(self) -> None:
        SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.SUCCESS,
            finished_at=timezone.now() - timedelta(hours=2),
        )

        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            output = self.call("--force")

        self.assertIn("Wrote 4 players", output)
        self.assertEqual(Player.objects.count(), 4)

    def test_runs_when_last_sync_is_stale(self) -> None:
        SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.SUCCESS,
            finished_at=timezone.now() - timedelta(hours=25),
        )

        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            output = self.call()

        self.assertIn("Wrote 4 players", output)

    def test_failed_run_does_not_block_the_next_attempt(self) -> None:
        SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.FAILED,
            finished_at=timezone.now(),
        )

        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            output = self.call()

        self.assertIn("Wrote 4 players", output)

    def test_api_error_becomes_command_error(self) -> None:
        client = FakeSleeperClient(error=SleeperAPIError("upstream exploded"))

        with (
            mock.patch(COMMAND_PATH, return_value=client),
            self.assertRaises(CommandError) as ctx,
        ):
            self.call()

        self.assertIn("upstream exploded", str(ctx.exception))


class SyncTrendingCommandTests(TestCase):
    def setUp(self) -> None:
        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            call_command("sync_players", stdout=StringIO())

    def call(self, *args: str, client: FakeSleeperClient | None = None) -> str:
        out = StringIO()
        trending = client or FakeSleeperClient(
            trending=[{"player_id": CHASE, "count": 4200}]
        )
        with mock.patch(COMMAND_PATH, return_value=trending):
            call_command("sync_trending", *args, stdout=out)
        return out.getvalue()

    def test_reports_counts(self) -> None:
        output = self.call()

        self.assertIn("Stored 2 trending row(s)", output)
        self.assertEqual(TrendingPlayer.objects.count(), 2)

    def test_reports_skipped_unknown_players(self) -> None:
        client = FakeSleeperClient(trending=[{"player_id": "unknown", "count": 1}])
        output = self.call(client=client)

        self.assertIn("skipped 2 unknown player(s)", output)

    def test_flags_are_passed_through(self) -> None:
        self.call("--lookback-hours", "48", "--limit", "10")
        self.assertEqual(TrendingPlayer.objects.filter(lookback_hours=48).count(), 2)

    def test_api_error_becomes_command_error(self) -> None:
        client = FakeSleeperClient(error=SleeperAPIError("trending down"))

        with self.assertRaises(CommandError) as ctx:
            self.call(client=client)

        self.assertIn("trending down", str(ctx.exception))


class SyncStatsCommandTests(TestCase):
    def setUp(self) -> None:
        with mock.patch(COMMAND_PATH, return_value=FakeSleeperClient()):
            call_command("sync_players", stdout=StringIO())

    def call(self, *args: str, client: FakeSleeperClient | None = None) -> str:
        out = StringIO()
        with mock.patch(COMMAND_PATH, return_value=client or FakeSleeperClient()):
            call_command("sync_stats", *args, stdout=out)
        return out.getvalue()

    def test_writes_and_reports(self) -> None:
        output = self.call("--season", "2024", "--week", "1")
        self.assertIn("stat row(s)", output)
        self.assertTrue(PlayerWeekStat.objects.exists())

    def test_kind_flag_restricts_to_projection(self) -> None:
        self.call("--season", "2024", "--week", "1", "--kind", "projection")
        self.assertTrue(PlayerWeekStat.objects.filter(kind="projection").exists())
        self.assertFalse(PlayerWeekStat.objects.filter(kind="stat").exists())

    def test_season_flag_bounds_both_ends(self) -> None:
        self.call("--season", "2024", "--week", "1")
        self.assertTrue(PlayerWeekStat.objects.filter(season=2024).exists())
        self.assertFalse(PlayerWeekStat.objects.exclude(season=2024).exists())

    def test_api_error_becomes_command_error(self) -> None:
        client = FakeSleeperClient(error=SleeperAPIError("stats down"))
        with self.assertRaises(CommandError) as ctx:
            self.call("--season", "2024", "--week", "1", client=client)
        self.assertIn("stats down", str(ctx.exception))


class StatsCoverageCommandTests(TestCase):
    player: Player

    @classmethod
    def setUpTestData(cls) -> None:
        cls.player = Player.objects.create(sleeper_id="p1", full_name="A Player")

    def make_stat(self, season: int, week: int, kind: str) -> None:
        PlayerWeekStat.objects.create(
            player=self.player, season=season, week=week, kind=kind
        )

    def call(self, *args: str) -> str:
        out = StringIO()
        call_command("stats_coverage", *args, stdout=out)
        return out.getvalue()

    def test_reports_counts_per_season_and_week(self) -> None:
        self.make_stat(2024, 1, "stat")
        self.make_stat(2024, 1, "projection")
        self.make_stat(2025, 5, "stat")

        output = self.call()

        self.assertIn("2024", output)
        self.assertIn("W01", output)
        self.assertIn("stat=1", output)
        self.assertIn("proj=1", output)
        self.assertIn("2025", output)
        self.assertIn("W05", output)

    def test_season_filter_excludes_others(self) -> None:
        self.make_stat(2024, 1, "stat")
        self.make_stat(2025, 1, "stat")

        output = self.call("--season", "2024")

        self.assertIn("2024", output)
        self.assertNotIn("2025", output)

    def test_empty_warns_cleanly(self) -> None:
        output = self.call()
        self.assertIn("No PlayerWeekStat rows found", output)


class RecomputeMetricsCommandTests(TestCase):
    def seed(self, season: int = 2024) -> None:
        player = Player.objects.create(sleeper_id="p", full_name="X", position="WR")
        PlayerWeekStat.objects.create(
            player=player, season=season, week=1, kind="stat", pts_ppr=10.0
        )

    def test_writes_and_reports(self) -> None:
        self.seed()
        out = StringIO()
        call_command("recompute_metrics", "--season", "2024", stdout=out)
        self.assertIn("season-metric row(s)", out.getvalue())
        self.assertTrue(PlayerSeasonMetrics.objects.exists())

    def test_default_recomputes_all(self) -> None:
        self.seed()
        call_command("recompute_metrics", stdout=StringIO())
        self.assertTrue(PlayerSeasonMetrics.objects.exists())


class MetricsReportCommandTests(TestCase):
    def make_metrics(
        self,
        name: str,
        season: int,
        ppg: float,
        position: str = "WR",
    ) -> None:
        player = Player.objects.create(
            sleeper_id=name, full_name=name, position=position
        )
        PlayerSeasonMetrics.objects.create(
            player=player,
            season=season,
            position=position,
            games_played=10,
            ppg_ppr=ppg,
            form_delta_ppr=1.0,
        )

    def call(self, *args: str) -> str:
        out = StringIO()
        call_command("metrics_report", *args, stdout=out)
        return out.getvalue()

    def test_lists_top_players_in_order(self) -> None:
        self.make_metrics("Alpha", 2024, 25.0)
        self.make_metrics("Bravo", 2024, 15.0)
        self.make_metrics("Charlie", 2023, 20.0)
        output = self.call()
        self.assertIn("2024", output)
        self.assertIn("2023", output)
        self.assertLess(output.index("Alpha"), output.index("Bravo"))  # 25 > 15

    def test_each_season_printed_once(self) -> None:
        # Two players with distinct ppg_ppr in one season must not duplicate the
        # season block (the Meta.ordering + distinct() trap).
        self.make_metrics("Alpha", 2024, 25.0)
        self.make_metrics("Bravo", 2024, 15.0)
        output = self.call()
        self.assertEqual(output.count("2024 (regular)"), 1)

    def test_top_limit(self) -> None:
        self.make_metrics("Alpha", 2024, 25.0)
        self.make_metrics("Bravo", 2024, 15.0)
        output = self.call("--top", "1")
        self.assertIn("Alpha", output)
        self.assertNotIn("Bravo", output)

    def test_position_filter(self) -> None:
        self.make_metrics("Wr1", 2024, 25.0, position="WR")
        self.make_metrics("Rb1", 2024, 20.0, position="RB")
        output = self.call("--position", "WR")
        self.assertIn("Wr1", output)
        self.assertNotIn("Rb1", output)

    def test_season_filter(self) -> None:
        self.make_metrics("Alpha", 2024, 25.0)
        self.make_metrics("Bravo", 2023, 20.0)
        output = self.call("--season", "2024")
        self.assertIn("2024", output)
        self.assertNotIn("Bravo", output)

    def test_empty_warns_cleanly(self) -> None:
        self.assertIn("No PlayerSeasonMetrics rows found", self.call())


class RecomputeValuesCommandTests(TestCase):
    def seed(self, season: int = 2024) -> None:
        player = Player.objects.create(sleeper_id="p", full_name="X", position="WR")
        PlayerSeasonMetrics.objects.create(
            player=player, season=season, position="WR", games_played=15, ppg_ppr=15.0
        )

    def test_writes_and_reports(self) -> None:
        self.seed()
        out = StringIO()
        call_command("recompute_values", "--season", "2024", stdout=out)
        self.assertIn("value(s) for season 2024", out.getvalue())
        self.assertTrue(PlayerValue.objects.exists())

    def test_no_metrics_errors(self) -> None:
        with self.assertRaises(CommandError):
            call_command("recompute_values", stdout=StringIO())

    def test_unknown_version_errors(self) -> None:
        self.seed()
        with self.assertRaises(CommandError):
            call_command(
                "recompute_values", "--model-version", "nope", stdout=StringIO()
            )
