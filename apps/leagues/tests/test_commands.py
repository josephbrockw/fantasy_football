from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.leagues.models import League, Team
from apps.leagues.tests.factories import ME, FakeLeagueClient, make_league, make_roster
from apps.leagues.tests.test_services import LEAGUE_ID, roster_client
from apps.players.services import sync_players
from apps.players.tests.utils import BRADY, FakeSleeperClient

CLIENT_PATH = "apps.leagues.services.SleeperClient"


class SyncLeagueCommandTests(TestCase):
    def call(self, *args: str, client: FakeLeagueClient | None = None) -> str:
        out = StringIO()
        with mock.patch(CLIENT_PATH, return_value=client or roster_client()):
            call_command("sync_league", *args, stdout=out)
        return out.getvalue()

    @override_settings(SLEEPER_USERNAME="dynastyguy")
    def test_syncs_and_reports(self) -> None:
        output = self.call()

        self.assertIn("The Dynasty", output)
        self.assertIn("Synced 1 league(s)", output)
        self.assertEqual(League.objects.count(), 1)
        self.assertEqual(Team.objects.count(), 1)

    @override_settings(SLEEPER_USERNAME="")
    def test_missing_username_is_a_command_error(self) -> None:
        with self.assertRaises(CommandError) as ctx:
            self.call()
        self.assertIn("SLEEPER_USERNAME", str(ctx.exception))

    @override_settings(SLEEPER_USERNAME="")
    def test_username_flag_overrides_settings(self) -> None:
        output = self.call("--username", "dynastyguy")
        self.assertIn("Synced 1 league(s)", output)

    @override_settings(SLEEPER_USERNAME="dynastyguy")
    def test_reports_when_no_leagues_found(self) -> None:
        output = self.call(client=FakeLeagueClient(user_leagues=[], season="2026"))
        self.assertIn("No leagues found", output)

    @override_settings(SLEEPER_USERNAME="dynastyguy")
    def test_reports_backfilled_players(self) -> None:
        sync_players(client=FakeSleeperClient())
        client = roster_client(starters=[BRADY], players=[BRADY])

        output = self.call(client=client)

        self.assertIn("Backfilled 1 player(s)", output)

    @override_settings(SLEEPER_USERNAME="dynastyguy")
    def test_season_flag_is_passed_through(self) -> None:
        league = make_league(LEAGUE_ID, "2025")
        client = FakeLeagueClient(
            user_leagues=[league],
            leagues={LEAGUE_ID: league},
            rosters={LEAGUE_ID: [make_roster(1, ME)]},
            users={LEAGUE_ID: []},
            season="2026",
        )

        self.call("--season", "2025", client=client)

        self.assertIn(f"get_user_leagues:{ME}:2025", client.calls)

    @override_settings(SLEEPER_USERNAME="nobody")
    def test_api_error_becomes_command_error(self) -> None:
        from apps.leagues.tests.factories import unknown_user_client

        with self.assertRaises(CommandError):
            self.call(client=unknown_user_client())
