from datetime import date

from django.test import SimpleTestCase, TestCase

from apps.players.models import Player, TrendingPlayer
from apps.players.services import (
    SEARCH_RANK_SENTINELS,
    is_live_player,
    player_from_payload,
    sync_players,
    sync_trending,
)
from apps.players.tests.utils import (
    BRADY,
    CHASE,
    LINEMAN,
    LOVE,
    SENTINEL_RANK,
    TEXANS_DEF,
    FakeSleeperClient,
    load_players_fixture,
)
from apps.sleeper.client import SleeperAPIError
from apps.sleeper.models import SyncRun


class IsLivePlayerTests(SimpleTestCase):
    """The filter that takes 12,200 Sleeper records down to ~1,043."""

    def setUp(self) -> None:
        self.fixture = load_players_fixture()

    def test_keeps_rostered_fantasy_players(self) -> None:
        for sleeper_id in (CHASE, LOVE, SENTINEL_RANK):
            with self.subTest(sleeper_id=sleeper_id):
                self.assertTrue(is_live_player(self.fixture[sleeper_id]))

    def test_keeps_team_defenses(self) -> None:
        self.assertTrue(is_live_player(self.fixture[TEXANS_DEF]))

    def test_excludes_retired_player_despite_active_flag(self) -> None:
        """The whole reason we filter on `team` and not `active`."""
        brady = self.fixture[BRADY]
        self.assertTrue(brady["active"])
        self.assertEqual(brady["status"], "Active")
        self.assertIsNone(brady["team"])
        self.assertFalse(is_live_player(brady))

    def test_excludes_non_fantasy_position(self) -> None:
        lineman = self.fixture[LINEMAN]
        self.assertTrue(lineman["team"])
        self.assertFalse(is_live_player(lineman))

    def test_excludes_empty_payload(self) -> None:
        self.assertFalse(is_live_player({}))

    def test_excludes_blank_team_string(self) -> None:
        self.assertFalse(is_live_player({"team": "", "position": "WR"}))


class PlayerFromPayloadTests(SimpleTestCase):
    def setUp(self) -> None:
        self.fixture = load_players_fixture()

    def test_maps_core_fields(self) -> None:
        player = player_from_payload(self.fixture[CHASE])
        self.assertEqual(player.sleeper_id, CHASE)
        self.assertEqual(player.full_name, "Ja'Marr Chase")
        self.assertEqual(player.position, "WR")
        self.assertEqual(player.team, "CIN")
        self.assertEqual(player.fantasy_positions, ["WR"])
        self.assertEqual(player.college, "LSU")
        self.assertEqual(player.birth_date, date(2000, 3, 1))
        self.assertEqual(player.search_rank, 3)

    def test_rookie_year_parsed_from_metadata_string(self) -> None:
        player = player_from_payload(self.fixture[LOVE])
        self.assertEqual(player.rookie_year, 2026)
        self.assertEqual(player.years_exp, 0)
        self.assertTrue(player.is_rookie)

    def test_defense_name_composed_from_parts(self) -> None:
        """Team defenses carry no full_name — only first/last."""
        payload = self.fixture[TEXANS_DEF]
        self.assertNotIn("full_name", payload)
        player = player_from_payload(payload)
        self.assertEqual(player.full_name, "Houston Texans")
        self.assertEqual(player.position, "DEF")

    def test_defense_tolerates_missing_optional_fields(self) -> None:
        player = player_from_payload(self.fixture[TEXANS_DEF])
        self.assertIsNone(player.age)
        self.assertIsNone(player.birth_date)
        self.assertIsNone(player.search_rank)
        self.assertEqual(player.status, "")
        self.assertEqual(player.college, "")

    def test_search_rank_sentinels_become_null(self) -> None:
        player = player_from_payload(self.fixture[SENTINEL_RANK])
        self.assertIn(self.fixture[SENTINEL_RANK]["search_rank"], SEARCH_RANK_SENTINELS)
        self.assertIsNone(player.search_rank)

    def test_both_sentinel_values_normalised(self) -> None:
        for sentinel in (999, 9999999):
            with self.subTest(sentinel=sentinel):
                player = player_from_payload(
                    {"player_id": "x", "search_rank": sentinel}
                )
                self.assertIsNone(player.search_rank)

    def test_malformed_values_degrade_to_none(self) -> None:
        player = player_from_payload(
            {
                "player_id": "x",
                "age": "not-a-number",
                "birth_date": "31-12-1999",
                "years_exp": None,
                "metadata": {"rookie_year": "unknown"},
            }
        )
        self.assertIsNone(player.age)
        self.assertIsNone(player.birth_date)
        self.assertIsNone(player.years_exp)
        self.assertIsNone(player.rookie_year)

    def test_null_metadata_is_tolerated(self) -> None:
        player = player_from_payload({"player_id": "x", "metadata": None})
        self.assertIsNone(player.rookie_year)

    def test_raw_payload_is_retained(self) -> None:
        player = player_from_payload(self.fixture[CHASE])
        self.assertEqual(player.raw, self.fixture[CHASE])


class SyncPlayersTests(TestCase):
    def test_stores_only_live_players(self) -> None:
        stats = sync_players(client=FakeSleeperClient())

        stored = set(Player.objects.values_list("sleeper_id", flat=True))
        self.assertEqual(stored, {CHASE, LOVE, TEXANS_DEF, SENTINEL_RANK})
        self.assertNotIn(BRADY, stored)
        self.assertNotIn(LINEMAN, stored)
        self.assertEqual(stats.written, 4)
        self.assertEqual(stats.skipped, 2)

    def test_include_inactive_stores_everything(self) -> None:
        stats = sync_players(client=FakeSleeperClient(), include_inactive=True)

        self.assertEqual(Player.objects.count(), 6)
        self.assertTrue(Player.objects.filter(sleeper_id=BRADY).exists())
        self.assertEqual(stats.skipped, 0)

    def test_is_idempotent(self) -> None:
        sync_players(client=FakeSleeperClient())
        sync_players(client=FakeSleeperClient())
        self.assertEqual(Player.objects.count(), 4)

    def test_updates_changed_fields_on_resync(self) -> None:
        sync_players(client=FakeSleeperClient())

        traded = load_players_fixture()
        traded[CHASE] = {**traded[CHASE], "team": "SF", "injury_status": "Questionable"}
        sync_players(client=FakeSleeperClient(players=traded))

        chase = Player.objects.get(sleeper_id=CHASE)
        self.assertEqual(chase.team, "SF")
        self.assertEqual(chase.injury_status, "Questionable")
        self.assertEqual(Player.objects.count(), 4)

    def test_dry_run_writes_nothing(self) -> None:
        stats = sync_players(client=FakeSleeperClient(), dry_run=True)
        self.assertEqual(Player.objects.count(), 0)
        self.assertEqual(stats.written, 4)

    def test_empty_dump_is_tolerated(self) -> None:
        stats = sync_players(client=FakeSleeperClient(players={}))
        self.assertEqual(stats.written, 0)
        self.assertEqual(Player.objects.count(), 0)

    def test_records_a_successful_sync_run(self) -> None:
        sync_players(client=FakeSleeperClient())

        run = SyncRun.objects.get(kind=SyncRun.Kind.PLAYERS)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertEqual(run.records_written, 4)
        self.assertEqual(run.records_skipped, 2)
        self.assertIsNotNone(run.finished_at)

    def test_records_a_failed_sync_run_and_reraises(self) -> None:
        client = FakeSleeperClient(error=SleeperAPIError("boom"))

        with self.assertRaises(SleeperAPIError):
            sync_players(client=client)

        run = SyncRun.objects.get(kind=SyncRun.Kind.PLAYERS)
        self.assertEqual(run.status, SyncRun.Status.FAILED)
        self.assertIn("boom", run.error)
        self.assertIsNotNone(run.finished_at)


class SyncTrendingTests(TestCase):
    def setUp(self) -> None:
        sync_players(client=FakeSleeperClient())

    def test_stores_counts_for_known_players(self) -> None:
        client = FakeSleeperClient(trending=[{"player_id": CHASE, "count": 5000}])

        stats = sync_trending(client=client)

        # The fake returns the same list for both add and drop.
        self.assertEqual(TrendingPlayer.objects.count(), 2)
        add = TrendingPlayer.objects.get(kind=TrendingPlayer.Kind.ADD)
        self.assertEqual(add.player.sleeper_id, CHASE)
        self.assertEqual(add.count, 5000)
        self.assertEqual(stats.written, 2)

    def test_skips_players_the_filter_never_stored(self) -> None:
        """Trending covers all of Sleeper, including players we don't hold."""
        client = FakeSleeperClient(
            trending=[
                {"player_id": CHASE, "count": 10},
                {"player_id": "not-in-our-table", "count": 99},
            ]
        )

        stats = sync_trending(client=client)

        self.assertEqual(TrendingPlayer.objects.count(), 2)
        self.assertEqual(stats.skipped, 2)

    def test_replaces_previous_rows(self) -> None:
        first = FakeSleeperClient(trending=[{"player_id": CHASE, "count": 1}])
        second = FakeSleeperClient(trending=[{"player_id": LOVE, "count": 7}])
        sync_trending(client=first)
        sync_trending(client=second)

        remaining = TrendingPlayer.objects.values_list("player__sleeper_id", flat=True)
        self.assertEqual(set(remaining), {LOVE})

    def test_missing_count_defaults_to_zero(self) -> None:
        sync_trending(client=FakeSleeperClient(trending=[{"player_id": CHASE}]))
        self.assertEqual(TrendingPlayer.objects.filter(count=0).count(), 2)

    def test_lookback_hours_is_recorded(self) -> None:
        sync_trending(
            client=FakeSleeperClient(trending=[{"player_id": CHASE, "count": 3}]),
            lookback_hours=48,
        )
        stored = TrendingPlayer.objects.filter(lookback_hours=48)
        self.assertEqual(stored.count(), 2)

    def test_empty_response_is_fine(self) -> None:
        stats = sync_trending(client=FakeSleeperClient(trending=[]))

        self.assertEqual(stats.written, 0)
        self.assertEqual(TrendingPlayer.objects.count(), 0)
        self.assertEqual(
            SyncRun.objects.get(kind=SyncRun.Kind.TRENDING).status,
            SyncRun.Status.SUCCESS,
        )

    def test_records_a_failed_sync_run(self) -> None:
        client = FakeSleeperClient(error=SleeperAPIError("down"))

        with self.assertRaises(SleeperAPIError):
            sync_trending(client=client)

        run = SyncRun.objects.get(kind=SyncRun.Kind.TRENDING)
        self.assertEqual(run.status, SyncRun.Status.FAILED)

    def test_str(self) -> None:
        client = FakeSleeperClient(trending=[{"player_id": CHASE, "count": 4}])
        sync_trending(client=client)
        row = TrendingPlayer.objects.get(kind=TrendingPlayer.Kind.ADD)
        self.assertIn("4", str(row))
