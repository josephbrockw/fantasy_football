from __future__ import annotations

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.enrichment.loaders import ProfileLoadError
from apps.enrichment.models import PlayerProfile
from apps.enrichment.tests.utils import FakeProfileLoader
from apps.players.tests.utils import FakeSleeperClient

# sync_profiles() builds its default loader here when none is injected.
LOADER_PATH = "apps.enrichment.services.DynastyProcessLoader"


class SyncProfilesCommandTests(TestCase):
    def setUp(self) -> None:
        with mock.patch(
            "apps.players.services.SleeperClient", return_value=FakeSleeperClient()
        ):
            call_command("sync_players", stdout=StringIO())

    def call(self, *args: str, loader: FakeProfileLoader | None = None) -> str:
        out = StringIO()
        with mock.patch(LOADER_PATH, return_value=loader or FakeProfileLoader()):
            call_command("sync_profiles", *args, stdout=out)
        return out.getvalue()

    def test_writes_and_reports(self) -> None:
        output = self.call()
        self.assertIn("profile(s)", output)
        self.assertTrue(PlayerProfile.objects.exists())

    def test_url_flag_builds_a_pinned_loader(self) -> None:
        path = "apps.enrichment.management.commands.sync_profiles.DynastyProcessLoader"
        with mock.patch(path, return_value=FakeProfileLoader()) as loader_cls:
            call_command(
                "sync_profiles", "--url", "http://example.test/x.csv", stdout=StringIO()
            )
        loader_cls.assert_called_once_with(url="http://example.test/x.csv")

    def test_source_flag_runs_combine_only(self) -> None:
        loader = FakeProfileLoader()
        self.call("--source", "combine", loader=loader)
        self.assertIn("fetch_combine", loader.calls)
        self.assertNotIn("fetch_player_ids", loader.calls)

    def test_wraps_load_error(self) -> None:
        loader = FakeProfileLoader(error=ProfileLoadError("down"))
        with self.assertRaises(CommandError) as ctx:
            self.call(loader=loader)
        self.assertIn("down", str(ctx.exception))
