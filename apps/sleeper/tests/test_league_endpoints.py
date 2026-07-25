"""Client coverage for the league endpoints added in PR 03."""

from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.sleeper.client import BASE_URL, SleeperAPIError, SleeperClient
from apps.sleeper.tests.test_client import fake_response


class LeagueEndpointTests(SimpleTestCase):
    def build(self, payload) -> tuple[SleeperClient, mock.Mock]:
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        session.get.return_value = fake_response(payload=payload)
        return SleeperClient(session=session), session

    def test_get_user(self) -> None:
        client, session = self.build({"user_id": "u1", "username": "dynastyguy"})

        user = client.get_user("dynastyguy")

        self.assertEqual(user["user_id"], "u1")
        self.assertEqual(session.get.call_args.args[0], f"{BASE_URL}/user/dynastyguy")

    def test_get_user_raises_for_unknown_username(self) -> None:
        """Sleeper answers 200 with a null body rather than a 404."""
        client, _ = self.build(None)

        with self.assertRaises(SleeperAPIError) as ctx:
            client.get_user("nobody")

        self.assertIn("nobody", str(ctx.exception))

    def test_get_user_leagues(self) -> None:
        client, session = self.build([{"league_id": "l1"}])

        leagues = client.get_user_leagues("u1", "2026")

        self.assertEqual(len(leagues), 1)
        self.assertEqual(
            session.get.call_args.args[0], f"{BASE_URL}/user/u1/leagues/nfl/2026"
        )

    def test_get_user_leagues_defaults_to_empty(self) -> None:
        client, _ = self.build(None)
        self.assertEqual(client.get_user_leagues("u1", "2026"), [])

    def test_get_league(self) -> None:
        client, session = self.build({"league_id": "l1", "name": "The Dynasty"})

        league = client.get_league("l1")

        assert league is not None
        self.assertEqual(league["name"], "The Dynasty")
        self.assertEqual(session.get.call_args.args[0], f"{BASE_URL}/league/l1")

    def test_get_league_returns_none_when_unknown(self) -> None:
        client, _ = self.build(None)
        self.assertIsNone(client.get_league("nope"))

    def test_get_league_rosters(self) -> None:
        client, session = self.build([{"roster_id": 1}])

        rosters = client.get_league_rosters("l1")

        self.assertEqual(len(rosters), 1)
        self.assertEqual(session.get.call_args.args[0], f"{BASE_URL}/league/l1/rosters")

    def test_get_league_users(self) -> None:
        client, session = self.build([{"user_id": "u1"}])

        users = client.get_league_users("l1")

        self.assertEqual(len(users), 1)
        self.assertEqual(session.get.call_args.args[0], f"{BASE_URL}/league/l1/users")

    def test_get_league_transactions(self) -> None:
        client, session = self.build([{"transaction_id": "t1", "type": "trade"}])

        txns = client.get_league_transactions("l1", 3)

        self.assertEqual(len(txns), 1)
        self.assertEqual(
            session.get.call_args.args[0], f"{BASE_URL}/league/l1/transactions/3"
        )

    def test_get_traded_picks(self) -> None:
        client, session = self.build([{"season": "2027", "round": 1}])

        picks = client.get_traded_picks("l1")

        self.assertEqual(len(picks), 1)
        self.assertEqual(
            session.get.call_args.args[0], f"{BASE_URL}/league/l1/traded_picks"
        )

    def test_list_endpoints_default_to_empty(self) -> None:
        client, _ = self.build(None)
        self.assertEqual(client.get_league_rosters("l1"), [])
        self.assertEqual(client.get_league_users("l1"), [])
        self.assertEqual(client.get_league_transactions("l1", 1), [])
        self.assertEqual(client.get_traded_picks("l1"), [])


class NotFoundHandlingTests(SimpleTestCase):
    """Sleeper answers 404 for an unknown league, but 200 for an unknown user.

    Verified against the live API. A purged ``previous_league_id`` must end the
    chain walk, not raise — so these two endpoints swallow the 404.
    """

    def build_404(self) -> SleeperClient:
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        session.get.return_value = fake_response(status_code=404, payload=None)
        return SleeperClient(session=session)

    def test_get_league_returns_none_on_404(self) -> None:
        self.assertIsNone(self.build_404().get_league("gone"))

    def test_get_league_rosters_returns_empty_on_404(self) -> None:
        self.assertEqual(self.build_404().get_league_rosters("gone"), [])

    def test_transaction_endpoints_return_empty_on_404(self) -> None:
        client = self.build_404()
        self.assertEqual(client.get_league_transactions("gone", 1), [])
        self.assertEqual(client.get_traded_picks("gone"), [])

    def test_other_endpoints_still_raise_on_404(self) -> None:
        client = self.build_404()
        for call in (
            lambda: client.get_nfl_state(),
            lambda: client.get_league_users("gone"),
            lambda: client.get_user("nobody"),
        ):
            with self.subTest(call=call), self.assertRaises(SleeperAPIError):
                call()

    def test_non_404_errors_still_raise_on_those_endpoints(self) -> None:
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        session.get.return_value = fake_response(status_code=500, payload=None)
        client = SleeperClient(session=session)

        with self.assertRaises(SleeperAPIError):
            client.get_league("l1")
        with self.assertRaises(SleeperAPIError):
            client.get_league_rosters("l1")
