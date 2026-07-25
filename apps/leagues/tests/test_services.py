from decimal import Decimal
from unittest import mock

from django.test import TestCase

from apps.leagues.models import (
    LeagueSeason,
    Manager,
    RosterSlot,
    SleeperAccount,
    Team,
)
from apps.leagues.services import (
    ensure_players_exist,
    starting_slots,
    sync_leagues,
)
from apps.leagues.tests.factories import (
    ME,
    RIVAL,
    FakeLeagueClient,
    make_league,
    make_roster,
    make_user,
    unknown_user_client,
)
from apps.players.models import Player
from apps.players.services import sync_players
from apps.players.tests.utils import (
    BRADY,
    CHASE,
    LOVE,
    TEXANS_DEF,
    FakeSleeperClient,
)
from apps.sleeper.client import SleeperAPIError
from apps.sleeper.models import SyncRun

LEAGUE_ID = "league_2026"


def roster_client(**roster_kwargs) -> FakeLeagueClient:
    league = make_league(LEAGUE_ID, "2026")
    return FakeLeagueClient(
        user_leagues=[league],
        leagues={LEAGUE_ID: league},
        rosters={LEAGUE_ID: [make_roster(1, ME, **roster_kwargs)]},
        users={LEAGUE_ID: [make_user(ME, team_name="My Squad")]},
        season="2026",
    )


class AccountTests(TestCase):
    def test_stores_the_account(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")

        account = SleeperAccount.objects.get()
        self.assertEqual(account.sleeper_user_id, ME)
        self.assertEqual(account.username, "dynastyguy")

    def test_flags_my_manager(self) -> None:
        client = FakeLeagueClient(
            user_leagues=[make_league(LEAGUE_ID, "2026")],
            leagues={LEAGUE_ID: make_league(LEAGUE_ID, "2026")},
            rosters={LEAGUE_ID: [make_roster(1, ME), make_roster(2, RIVAL)]},
            users={
                LEAGUE_ID: [
                    make_user(ME),
                    make_user(RIVAL, "rival", "Rival"),
                ]
            },
            season="2026",
        )

        sync_leagues(client=client, username="dynastyguy")

        self.assertTrue(Manager.objects.get(sleeper_user_id=ME).is_me)
        self.assertFalse(Manager.objects.get(sleeper_user_id=RIVAL).is_me)

    def test_unknown_username_raises(self) -> None:
        with self.assertRaises(SleeperAPIError):
            sync_leagues(client=unknown_user_client(), username="nobody")

    def test_missing_username_raises(self) -> None:
        with self.assertRaises(SleeperAPIError):
            sync_leagues(client=roster_client(), username="")

    def test_username_change_reuses_the_account_by_user_id(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")

        renamed = roster_client()
        renamed.user = make_user(ME, username="newhandle")
        sync_leagues(client=renamed, username="newhandle")

        self.assertEqual(SleeperAccount.objects.count(), 1)
        self.assertEqual(SleeperAccount.objects.get().username, "newhandle")


class LeagueSeasonTests(TestCase):
    def test_stores_league_settings(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")

        season = LeagueSeason.objects.get()
        self.assertEqual(season.season, "2026")
        self.assertEqual(season.sleeper_league_id, LEAGUE_ID)
        self.assertEqual(season.scoring_settings["rec"], 1.0)
        self.assertEqual(season.settings["taxi_slots"], 4)
        self.assertIn("QB", season.roster_positions)

    def test_fantasy_positions_excludes_flex_placeholders(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")

        positions = LeagueSeason.objects.get().fantasy_positions
        self.assertNotIn("BN", positions)
        self.assertNotIn("FLEX", positions)
        self.assertIn("QB", positions)

    def test_current_season_is_the_latest(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")
        league = LeagueSeason.objects.get().league
        current = league.current_season
        assert current is not None
        self.assertEqual(current.season, "2026")


class TeamTests(TestCase):
    def test_stores_record_and_points(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")

        team = Team.objects.get()
        self.assertEqual(team.wins, 8)
        self.assertEqual(team.losses, 5)
        self.assertEqual(team.team_name, "My Squad")
        self.assertEqual(team.record, "8-5")
        self.assertEqual(team.waiver_budget_used, 40)

    def test_points_combine_whole_and_hundredths(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")

        team = Team.objects.get()
        self.assertEqual(team.points_for, Decimal("1450.55"))
        self.assertEqual(team.points_against, Decimal("1300.25"))

    def test_record_includes_ties_when_present(self) -> None:
        sync_leagues(client=roster_client(ties=2), username="dynastyguy")
        self.assertEqual(Team.objects.get().record, "8-5-2")

    def test_orphan_roster_gets_a_null_manager(self) -> None:
        league = make_league(LEAGUE_ID, "2026")
        client = FakeLeagueClient(
            user_leagues=[league],
            leagues={LEAGUE_ID: league},
            rosters={LEAGUE_ID: [make_roster(1, None)]},
            users={LEAGUE_ID: []},
            season="2026",
        )

        sync_leagues(client=client, username="dynastyguy")

        team = Team.objects.get()
        self.assertIsNone(team.manager)
        self.assertEqual(str(team), "Roster 1")

    def test_unparseable_points_degrade_to_zero(self) -> None:
        client = roster_client(fpts="junk", fpts_decimal=None)
        sync_leagues(client=client, username="dynastyguy")

        self.assertEqual(Team.objects.get().points_for, Decimal(0))

    def test_missing_settings_default_to_zero(self) -> None:
        league = make_league(LEAGUE_ID, "2026")
        roster = {"roster_id": 1, "owner_id": ME, "players": []}
        client = FakeLeagueClient(
            user_leagues=[league],
            leagues={LEAGUE_ID: league},
            rosters={LEAGUE_ID: [roster]},
            users={LEAGUE_ID: [make_user(ME)]},
            season="2026",
        )

        sync_leagues(client=client, username="dynastyguy")

        team = Team.objects.get()
        self.assertEqual(team.wins, 0)
        self.assertEqual(team.points_for, Decimal(0))


class RosterSlotTests(TestCase):
    def setUp(self) -> None:
        sync_players(client=FakeSleeperClient())

    def test_slots_derived_from_the_roster_lists(self) -> None:
        client = roster_client(
            starters=[CHASE],
            players=[CHASE, LOVE, TEXANS_DEF],
            taxi=[LOVE],
            reserve=[TEXANS_DEF],
        )
        sync_leagues(client=client, username="dynastyguy")

        slots = {slot.player.sleeper_id: slot.slot for slot in RosterSlot.objects.all()}
        self.assertEqual(
            slots,
            {
                CHASE: RosterSlot.Slot.STARTER,
                LOVE: RosterSlot.Slot.TAXI,
                TEXANS_DEF: RosterSlot.Slot.IR,
            },
        )

    def test_bench_is_everything_not_otherwise_assigned(self) -> None:
        client = roster_client(starters=[CHASE], players=[CHASE, LOVE])
        sync_leagues(client=client, username="dynastyguy")

        bench = RosterSlot.objects.get(slot=RosterSlot.Slot.BENCH)
        self.assertEqual(bench.player.sleeper_id, LOVE)

    def test_starter_wins_over_bench_listing(self) -> None:
        """Sleeper lists starters in `players` too; starter must take priority."""
        client = roster_client(starters=[CHASE], players=[CHASE])
        sync_leagues(client=client, username="dynastyguy")

        self.assertEqual(RosterSlot.objects.get().slot, RosterSlot.Slot.STARTER)

    def test_empty_starting_slots_are_ignored(self) -> None:
        """Sleeper uses the string "0" for an unfilled starting slot."""
        client = roster_client(starters=[CHASE, "0", "0"], players=[CHASE])
        sync_leagues(client=client, username="dynastyguy")

        self.assertEqual(RosterSlot.objects.count(), 1)
        self.assertFalse(Player.objects.filter(sleeper_id="0").exists())

    def test_resync_rebuilds_rather_than_duplicating(self) -> None:
        sync_leagues(
            client=roster_client(starters=[CHASE], players=[CHASE, LOVE]),
            username="dynastyguy",
        )
        sync_leagues(
            client=roster_client(starters=[LOVE], players=[LOVE]),
            username="dynastyguy",
        )

        self.assertEqual(RosterSlot.objects.count(), 1)
        remaining = RosterSlot.objects.get()
        self.assertEqual(remaining.player.sleeper_id, LOVE)
        self.assertEqual(remaining.slot, RosterSlot.Slot.STARTER)

    def test_empty_roster_is_fine(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")
        self.assertEqual(RosterSlot.objects.count(), 0)


class LineupAlignmentTests(TestCase):
    """Sleeper's `starters` array aligns index-for-index with the starting slots.

    Losing that alignment makes a legal superflex lineup look broken — two
    quarterbacks starting with no indication that one fills the SUPER_FLEX.
    """

    def setUp(self) -> None:
        sync_players(client=FakeSleeperClient())

    def superflex_client(self, starters: list[str]) -> FakeLeagueClient:
        league = make_league(
            LEAGUE_ID,
            "2026",
            roster_positions=["QB", "RB", "SUPER_FLEX", "BN", "BN", "TAXI"],
        )
        return FakeLeagueClient(
            user_leagues=[league],
            leagues={LEAGUE_ID: league},
            rosters={
                LEAGUE_ID: [
                    make_roster(1, ME, starters=starters, players=list(starters))
                ]
            },
            users={LEAGUE_ID: [make_user(ME)]},
            season="2026",
        )

    def test_starting_slots_drops_non_starting_entries(self) -> None:
        positions = ["QB", "RB", "FLEX", "BN", "BN", "IR", "TAXI"]
        self.assertEqual(starting_slots(positions), ["QB", "RB", "FLEX"])

    def test_lineup_position_recorded_per_index(self) -> None:
        client = self.superflex_client([CHASE, LOVE, TEXANS_DEF])
        sync_leagues(client=client, username="dynastyguy")

        by_player = {
            slot.player.sleeper_id: slot
            for slot in RosterSlot.objects.select_related("player")
        }
        self.assertEqual(by_player[CHASE].lineup_position, "QB")
        self.assertEqual(by_player[CHASE].lineup_order, 0)
        self.assertEqual(by_player[LOVE].lineup_position, "RB")
        self.assertEqual(by_player[LOVE].lineup_order, 1)
        self.assertEqual(by_player[TEXANS_DEF].lineup_position, "SUPER_FLEX")
        self.assertEqual(by_player[TEXANS_DEF].lineup_order, 2)

    def test_empty_slot_shifts_nothing(self) -> None:
        """A "0" placeholder must not slide later starters up an index."""
        client = self.superflex_client([CHASE, "0", TEXANS_DEF])
        sync_leagues(client=client, username="dynastyguy")

        superflex = RosterSlot.objects.get(player__sleeper_id=TEXANS_DEF)
        self.assertEqual(superflex.lineup_order, 2)
        self.assertEqual(superflex.lineup_position, "SUPER_FLEX")
        self.assertEqual(RosterSlot.objects.count(), 2)

    def test_reserves_carry_no_lineup_position(self) -> None:
        client = roster_client(starters=[CHASE], players=[CHASE, LOVE], taxi=[LOVE])
        sync_leagues(client=client, username="dynastyguy")

        taxi = RosterSlot.objects.get(slot=RosterSlot.Slot.TAXI)
        self.assertEqual(taxi.lineup_position, "")
        self.assertIsNone(taxi.lineup_order)

    def test_starters_beyond_declared_slots_are_tolerated(self) -> None:
        """A malformed league shouldn't crash the sync."""
        league = make_league(LEAGUE_ID, "2026", roster_positions=["QB"])
        client = FakeLeagueClient(
            user_leagues=[league],
            leagues={LEAGUE_ID: league},
            rosters={
                LEAGUE_ID: [
                    make_roster(1, ME, starters=[CHASE, LOVE], players=[CHASE, LOVE])
                ]
            },
            users={LEAGUE_ID: [make_user(ME)]},
            season="2026",
        )

        sync_leagues(client=client, username="dynastyguy")

        overflow = RosterSlot.objects.get(player__sleeper_id=LOVE)
        self.assertEqual(overflow.lineup_position, "")
        self.assertEqual(overflow.lineup_order, 1)


class EnsurePlayersExistTests(TestCase):
    """Referential integrity for players the live-player filter excludes."""

    def test_backfills_a_player_missing_from_the_filtered_table(self) -> None:
        sync_players(client=FakeSleeperClient())
        self.assertFalse(Player.objects.filter(sleeper_id=BRADY).exists())

        client = roster_client(starters=[BRADY], players=[BRADY])
        sync_leagues(client=client, username="dynastyguy")

        self.assertTrue(Player.objects.filter(sleeper_id=BRADY).exists())
        self.assertEqual(RosterSlot.objects.get().player.sleeper_id, BRADY)

    def test_reports_the_backfill_count(self) -> None:
        sync_players(client=FakeSleeperClient())
        client = roster_client(starters=[BRADY], players=[BRADY])

        stats = sync_leagues(client=client, username="dynastyguy")

        self.assertEqual(stats.players_backfilled, 1)

    def test_player_unknown_to_sleeper_gets_a_stub(self) -> None:
        client = roster_client(players=["nonexistent_id"])
        sync_leagues(client=client, username="dynastyguy")

        stub = Player.objects.get(sleeper_id="nonexistent_id")
        self.assertIn("Unknown player", stub.full_name)
        self.assertEqual(RosterSlot.objects.count(), 1)

    def test_no_dump_fetched_when_nothing_is_missing(self) -> None:
        sync_players(client=FakeSleeperClient())
        client = roster_client(starters=[CHASE], players=[CHASE])

        sync_leagues(client=client, username="dynastyguy")

        self.assertNotIn("get_all_players", client.calls)

    def test_empty_id_set_is_a_no_op(self) -> None:
        client = FakeLeagueClient()
        self.assertEqual(ensure_players_exist(client, set()), 0)
        self.assertEqual(ensure_players_exist(client, {""}), 0)


class SyncRunTrackingTests(TestCase):
    def test_records_success(self) -> None:
        sync_leagues(client=roster_client(), username="dynastyguy")

        run = SyncRun.objects.get(kind=SyncRun.Kind.LEAGUE)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)

    def test_records_failure(self) -> None:
        with self.assertRaises(SleeperAPIError):
            sync_leagues(client=unknown_user_client(), username="nobody")

        run = SyncRun.objects.get(kind=SyncRun.Kind.LEAGUE)
        self.assertEqual(run.status, SyncRun.Status.FAILED)

    def test_failure_record_survives_the_rollback(self) -> None:
        """The SyncRun must outlive the transaction it was auditing.

        The league writes are atomic, so a mid-sync failure rolls them back —
        but the audit row is created outside that transaction and must remain.
        """
        client = roster_client()
        client.rosters = {LEAGUE_ID: [make_roster(1, ME)]}
        # Blow up after the league and team have already been written.
        client.get_league_rosters = mock.Mock(  # type: ignore[method-assign]
            side_effect=SleeperAPIError("died mid-sync")
        )

        with self.assertRaises(SleeperAPIError):
            sync_leagues(client=client, username="dynastyguy")

        run = SyncRun.objects.get(kind=SyncRun.Kind.LEAGUE)
        self.assertEqual(run.status, SyncRun.Status.FAILED)
        self.assertIn("died mid-sync", run.error)
        # ...and the partial league write is gone.
        self.assertEqual(LeagueSeason.objects.count(), 0)
        self.assertEqual(Team.objects.count(), 0)

    def test_no_leagues_is_not_an_error(self) -> None:
        client = FakeLeagueClient(user_leagues=[], season="2026")

        stats = sync_leagues(client=client, username="dynastyguy")

        self.assertEqual(stats.seasons, 0)
        self.assertEqual(
            SyncRun.objects.get(kind=SyncRun.Kind.LEAGUE).status,
            SyncRun.Status.SUCCESS,
        )

    def test_season_defaults_to_current_nfl_season(self) -> None:
        client = roster_client()
        sync_leagues(client=client, username="dynastyguy")

        self.assertIn("get_nfl_state", client.calls)
        self.assertIn(f"get_user_leagues:{ME}:2026", client.calls)

    def test_explicit_season_skips_the_state_lookup(self) -> None:
        client = roster_client()
        sync_leagues(client=client, username="dynastyguy", season="2025")

        self.assertNotIn("get_nfl_state", client.calls)
        self.assertIn(f"get_user_leagues:{ME}:2025", client.calls)
