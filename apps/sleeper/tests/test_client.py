from typing import cast
from unittest import mock

import requests
from django.test import SimpleTestCase
from requests.adapters import HTTPAdapter

from apps.sleeper.client import (
    BASE_URL,
    DEFAULT_TIMEOUT,
    SleeperAPIError,
    SleeperClient,
)


def fake_response(status_code: int = 200, payload=None, invalid_json: bool = False):
    response = mock.Mock(spec=requests.Response)
    response.status_code = status_code
    if invalid_json:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload
    return response


class SleeperClientTests(SimpleTestCase):
    def build(self, response=None, side_effect=None) -> tuple[SleeperClient, mock.Mock]:
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        if side_effect is not None:
            session.get.side_effect = side_effect
        else:
            session.get.return_value = response or fake_response(payload={})
        return SleeperClient(session=session), session

    def test_builds_expected_url_and_passes_timeout(self) -> None:
        client, session = self.build(fake_response(payload={"season": "2026"}))

        result = client.get_nfl_state()

        self.assertEqual(result, {"season": "2026"})
        session.get.assert_called_once_with(
            f"{BASE_URL}/state/nfl", params=None, timeout=DEFAULT_TIMEOUT
        )

    def test_get_all_players_hits_the_dump_endpoint(self) -> None:
        client, session = self.build(fake_response(payload={"7564": {}}))

        client.get_all_players()

        self.assertEqual(session.get.call_args.args[0], f"{BASE_URL}/players/nfl")

    def test_trending_passes_query_params(self) -> None:
        client, session = self.build(fake_response(payload=[]))

        client.get_trending_players(kind="drop", lookback_hours=48, limit=10)

        self.assertEqual(
            session.get.call_args.args[0], f"{BASE_URL}/players/nfl/trending/drop"
        )
        self.assertEqual(
            session.get.call_args.kwargs["params"],
            {"lookback_hours": 48, "limit": 10},
        )

    def test_trending_rejects_unknown_kind(self) -> None:
        client, _ = self.build()
        with self.assertRaises(ValueError):
            client.get_trending_players(kind="sideways")

    def test_non_200_raises(self) -> None:
        client, _ = self.build(fake_response(status_code=503))
        with self.assertRaises(SleeperAPIError) as ctx:
            client.get_nfl_state()
        self.assertIn("503", str(ctx.exception))

    def test_invalid_json_raises(self) -> None:
        client, _ = self.build(fake_response(invalid_json=True))
        with self.assertRaises(SleeperAPIError) as ctx:
            client.get_nfl_state()
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_transport_error_raises(self) -> None:
        client, _ = self.build(side_effect=requests.ConnectionError("refused"))
        with self.assertRaises(SleeperAPIError) as ctx:
            client.get_nfl_state()
        self.assertIn("refused", str(ctx.exception))

    def test_base_url_trailing_slash_is_normalised(self) -> None:
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        session.get.return_value = fake_response(payload={})
        client = SleeperClient(base_url="https://example.test/v1/", session=session)

        client.get_nfl_state()

        self.assertEqual(
            session.get.call_args.args[0], "https://example.test/v1/state/nfl"
        )


class SessionConfigTests(SimpleTestCase):
    def test_retries_configured_for_transient_failures(self) -> None:
        session = SleeperClient.build_session()
        adapter = cast(HTTPAdapter, session.get_adapter("https://api.sleeper.app"))
        retry = adapter.max_retries

        self.assertEqual(retry.total, 3)
        self.assertIn(429, retry.status_forcelist)
        self.assertIn(503, retry.status_forcelist)
        self.assertGreater(retry.backoff_factor, 0)

    def test_sets_a_user_agent(self) -> None:
        session = SleeperClient.build_session()
        self.assertIn("dynasty-hq", session.headers["User-Agent"])

    def test_default_session_is_built_when_not_supplied(self) -> None:
        client = SleeperClient()
        self.assertIsInstance(client.session, requests.Session)
