from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.players.models import Player, PlayerWeekStat, TrendingPlayer
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
