from __future__ import annotations

from django.test import TestCase

from apps.players.models import PlayerWeekStat
from apps.players.services import MIN_SEASON, ingest_week, sync_players, sync_stats
from apps.players.tests.utils import CHASE, FakeSleeperClient
from apps.sleeper.client import SleeperAPIError
from apps.sleeper.models import SyncRun


class StatsSyncFixture(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        # Populate the Player universe so CHASE / LOVE are "known" ids.
        sync_players(client=FakeSleeperClient())


class IngestWeekTests(StatsSyncFixture):
    def test_writes_known_skips_unknown(self) -> None:
        written, skipped = ingest_week(
            FakeSleeperClient(), 2024, 1, PlayerWeekStat.Kind.STAT
        )
        self.assertEqual(written, 2)  # 7564 + 13287 are known
        self.assertEqual(skipped, 1)  # 999999 is not in the Player table
        chase = PlayerWeekStat.objects.get(player__sleeper_id=CHASE, kind="stat")
        self.assertEqual(chase.pts_ppr, 24.5)
        self.assertEqual(chase.pts_half_ppr, 21.0)
        self.assertIn("rec_yd", chase.stats)

    def test_bad_points_value_becomes_null(self) -> None:
        client = FakeSleeperClient(stats={CHASE: {"pts_ppr": "N/A"}})
        ingest_week(client, 2024, 1, PlayerWeekStat.Kind.STAT)
        row = PlayerWeekStat.objects.get(player__sleeper_id=CHASE, kind="stat")
        self.assertIsNone(row.pts_ppr)

    def test_all_unknown_writes_nothing(self) -> None:
        client = FakeSleeperClient(stats={"999999": {"pts_ppr": 1.0}})
        written, skipped = ingest_week(client, 2024, 1, PlayerWeekStat.Kind.STAT)
        self.assertEqual((written, skipped), (0, 1))
        self.assertFalse(PlayerWeekStat.objects.exists())


class SyncStatsTests(StatsSyncFixture):
    def test_writes_both_kinds_for_same_week(self) -> None:
        sync_stats(
            client=FakeSleeperClient(), start_season=2024, end_season=2024, weeks=[1]
        )
        self.assertTrue(PlayerWeekStat.objects.filter(kind="stat").exists())
        self.assertTrue(PlayerWeekStat.objects.filter(kind="projection").exists())
        self.assertEqual(
            PlayerWeekStat.objects.filter(
                player__sleeper_id=CHASE, season=2024, week=1
            ).count(),
            2,  # a stat and a projection coexist
        )

    def test_is_idempotent_and_updates_in_place(self) -> None:
        sync_stats(
            client=FakeSleeperClient(),
            start_season=2024,
            end_season=2024,
            weeks=[1],
            kinds=["stat"],
        )
        first = PlayerWeekStat.objects.get(
            player__sleeper_id=CHASE, season=2024, week=1, kind="stat"
        )

        # Re-run with a changed value for CHASE.
        sync_stats(
            client=FakeSleeperClient(stats={CHASE: {"pts_ppr": 30.0}}),
            start_season=2024,
            end_season=2024,
            weeks=[1],
            kinds=["stat"],
        )
        rows = PlayerWeekStat.objects.filter(
            player__sleeper_id=CHASE, season=2024, week=1, kind="stat"
        )
        self.assertEqual(rows.count(), 1)  # upserted, not duplicated
        updated = rows.get()
        self.assertEqual(updated.pts_ppr, 30.0)
        self.assertGreater(updated.updated_at, first.updated_at)  # auto_now workaround

    def test_empty_week_is_not_an_error(self) -> None:
        client = FakeSleeperClient(stats={}, projections={})
        sync_stats(client=client, start_season=2024, end_season=2024, weeks=[1])
        self.assertEqual(PlayerWeekStat.objects.count(), 0)
        run = SyncRun.objects.get(kind=SyncRun.Kind.STATS)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)

    def test_default_range_uses_nfl_state(self) -> None:
        client = FakeSleeperClient(stats={}, projections={}, season="2020")
        sync_stats(client=client)  # no bounds → MIN_SEASON..2020
        self.assertIn("get_nfl_state", client.calls)
        seasons = {
            call.split(":")[1]
            for call in client.calls
            if call.startswith("get_player_stats")
        }
        self.assertIn(str(MIN_SEASON), seasons)
        self.assertIn("2020", seasons)
        self.assertNotIn("2021", seasons)  # stops at the current season

    def test_dry_run_writes_nothing(self) -> None:
        stats = sync_stats(
            client=FakeSleeperClient(),
            start_season=2024,
            end_season=2024,
            weeks=[1],
            dry_run=True,
        )
        self.assertEqual(PlayerWeekStat.objects.count(), 0)
        self.assertGreater(stats.written, 0)  # counted, not stored

    def test_records_failure(self) -> None:
        client = FakeSleeperClient(error=SleeperAPIError("boom"))
        with self.assertRaises(SleeperAPIError):
            sync_stats(client=client, start_season=2024, end_season=2024, weeks=[1])
        run = SyncRun.objects.get(kind=SyncRun.Kind.STATS)
        self.assertEqual(run.status, SyncRun.Status.FAILED)
