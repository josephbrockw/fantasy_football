from __future__ import annotations

from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.enrichment.loaders import DynastyProcessLoader, ProfileLoadError


class DynastyProcessLoaderTests(SimpleTestCase):
    def build(
        self, status_code: int = 200, text: str = "", side_effect=None
    ) -> tuple[DynastyProcessLoader, mock.Mock]:
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        if side_effect is not None:
            session.get.side_effect = side_effect
        else:
            response = mock.Mock(spec=requests.Response)
            response.status_code = status_code
            response.text = text
            session.get.return_value = response
        return DynastyProcessLoader(session=session), session

    def test_parses_csv_into_dict_rows(self) -> None:
        loader, session = self.build(text="sleeper_id,draft_year\n7564,2021\n")
        rows = loader.fetch_player_ids()
        self.assertEqual(rows, [{"sleeper_id": "7564", "draft_year": "2021"}])
        self.assertIn("db_playerids", session.get.call_args.args[0])

    def test_fetch_combine_parses_rows(self) -> None:
        loader, session = self.build(text="pfr_id,forty\nChasJa00,4.38\n")
        rows = loader.fetch_combine()
        self.assertEqual(rows, [{"pfr_id": "ChasJa00", "forty": "4.38"}])
        self.assertIn("combine", session.get.call_args.args[0])

    def test_non_200_raises(self) -> None:
        loader, _ = self.build(status_code=404)
        with self.assertRaises(ProfileLoadError) as ctx:
            loader.fetch_player_ids()
        self.assertIn("404", str(ctx.exception))

    def test_transport_error_raises(self) -> None:
        loader, _ = self.build(side_effect=requests.ConnectionError("refused"))
        with self.assertRaises(ProfileLoadError) as ctx:
            loader.fetch_player_ids()
        self.assertIn("refused", str(ctx.exception))

    def test_default_session_is_built_with_user_agent(self) -> None:
        loader = DynastyProcessLoader()
        self.assertIsInstance(loader.session, requests.Session)
        self.assertIn("dynasty-hq", loader.session.headers["User-Agent"])
