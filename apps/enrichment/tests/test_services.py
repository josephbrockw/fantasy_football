from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from apps.enrichment.loaders import ProfileLoadError
from apps.enrichment.models import PlayerProfile
from apps.enrichment.services import (
    _as_float,
    _bmi,
    _height_to_inches,
    sync_profiles,
)
from apps.enrichment.tests.utils import FakeProfileLoader
from apps.players.models import Player
from apps.players.services import sync_players
from apps.players.tests.utils import CHASE, FakeSleeperClient
from apps.sleeper.models import SyncRun

IDS = ("ids",)
COMBINE = ("combine",)


class ProfileSyncFixture(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        # Populate the Player universe so CHASE / LOVE are known sleeper_ids.
        sync_players(client=FakeSleeperClient())


class DraftCapitalSyncTests(ProfileSyncFixture):
    def test_writes_matched_skips_unmatched(self) -> None:
        stats = sync_profiles(loader=FakeProfileLoader(), sources=IDS)
        # CHASE + LOVE match; the untracked id and the empty-sleeper_id row skip.
        self.assertEqual(stats.written, 2)
        self.assertEqual(stats.skipped, 2)
        chase = PlayerProfile.objects.get(player__sleeper_id=CHASE)
        self.assertEqual(chase.draft_year, 2021)
        self.assertEqual(chase.draft_round, 1)
        self.assertEqual(chase.draft_pick, 5)  # draft_ovr
        self.assertEqual(chase.draft_team, "CIN")
        self.assertEqual(chase.pfr_id, "ChasJa00")

    def test_is_idempotent_and_updates_in_place(self) -> None:
        row = {
            "sleeper_id": CHASE,
            "draft_year": "2021",
            "draft_round": "1",
            "draft_ovr": "5",
            "draft_team": "CIN",
            "pfr_id": "ChasJa00",
            "gsis_id": "",
        }
        sync_profiles(loader=FakeProfileLoader(rows=[row]), sources=IDS)
        first = PlayerProfile.objects.get(player__sleeper_id=CHASE)

        sync_profiles(
            loader=FakeProfileLoader(rows=[{**row, "draft_ovr": "6"}]), sources=IDS
        )
        rows = PlayerProfile.objects.filter(player__sleeper_id=CHASE)
        self.assertEqual(rows.count(), 1)  # upserted, not duplicated
        updated = rows.get()
        self.assertEqual(updated.draft_pick, 6)
        self.assertGreater(updated.updated_at, first.updated_at)

    def test_all_unmatched_writes_nothing(self) -> None:
        loader = FakeProfileLoader(
            rows=[{"sleeper_id": "999999", "draft_year": "2020"}]
        )
        stats = sync_profiles(loader=loader, sources=IDS)
        self.assertEqual((stats.written, stats.skipped), (0, 1))
        self.assertFalse(PlayerProfile.objects.exists())

    def test_coerces_missing_and_bad_draft_values(self) -> None:
        row = {
            "sleeper_id": CHASE,
            "draft_year": "",
            "draft_round": "N/A",
            "draft_ovr": "",
        }
        sync_profiles(loader=FakeProfileLoader(rows=[row]), sources=IDS)
        profile = PlayerProfile.objects.get(player__sleeper_id=CHASE)
        self.assertIsNone(profile.draft_year)  # empty → None
        self.assertIsNone(profile.draft_round)  # non-numeric → None
        self.assertIsNone(profile.draft_pick)

    def test_records_syncrun(self) -> None:
        sync_profiles(loader=FakeProfileLoader(), sources=IDS)
        run = SyncRun.objects.get(kind=SyncRun.Kind.PROFILES)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertEqual(run.records_written, 2)
        self.assertEqual(run.records_skipped, 2)

    def test_records_failure(self) -> None:
        loader = FakeProfileLoader(error=ProfileLoadError("boom"))
        with self.assertRaises(ProfileLoadError):
            sync_profiles(loader=loader, sources=IDS)
        run = SyncRun.objects.get(kind=SyncRun.Kind.PROFILES)
        self.assertEqual(run.status, SyncRun.Status.FAILED)

    def test_dry_run_writes_nothing(self) -> None:
        stats = sync_profiles(loader=FakeProfileLoader(), dry_run=True, sources=IDS)
        self.assertEqual(PlayerProfile.objects.count(), 0)
        self.assertEqual(stats.written, 2)

    def test_draft_refresh_leaves_combine_untouched(self) -> None:
        # A combine value written earlier must survive a draft-only refresh.
        chase = Player.objects.get(sleeper_id=CHASE)
        PlayerProfile.objects.create(player=chase, forty=4.4)
        sync_profiles(loader=FakeProfileLoader(), sources=IDS)
        profile = PlayerProfile.objects.get(player=chase)
        self.assertEqual(profile.forty, 4.4)  # untouched by the draft pass
        self.assertEqual(profile.draft_year, 2021)  # draft fields refreshed


class CombineSyncTests(ProfileSyncFixture):
    def seed_draft(self) -> None:
        # Populate pfr_ids so the combine pass has profiles to join onto.
        sync_profiles(loader=FakeProfileLoader(), sources=IDS)

    def test_updates_matched_skips_unmatched(self) -> None:
        self.seed_draft()
        stats = sync_profiles(loader=FakeProfileLoader(), sources=COMBINE)
        # ChasJa00 joins CHASE's profile; NobodyXx00 has no tracked profile.
        self.assertEqual(stats.written, 1)
        self.assertEqual(stats.skipped, 1)
        chase = PlayerProfile.objects.get(player__sleeper_id=CHASE)
        self.assertEqual(chase.height_inches, 72)  # "6-0"
        self.assertEqual(chase.weight_lbs, 201)
        self.assertEqual(chase.forty, 4.38)
        self.assertEqual(chase.vertical, 38.5)
        self.assertEqual(chase.broad_jump, 127)
        self.assertIsNone(chase.bench)  # blank in the fixture
        self.assertIsNotNone(chase.bmi)

    def test_preserves_draft_capital(self) -> None:
        self.seed_draft()
        sync_profiles(loader=FakeProfileLoader(), sources=COMBINE)
        chase = PlayerProfile.objects.get(player__sleeper_id=CHASE)
        self.assertEqual(chase.draft_year, 2021)  # untouched by combine
        self.assertEqual(chase.draft_pick, 5)

    def test_both_sources_share_one_run(self) -> None:
        stats = sync_profiles(loader=FakeProfileLoader())  # default both
        self.assertEqual(stats.written, 3)  # 2 draft + 1 combine
        self.assertEqual(stats.skipped, 3)  # 2 draft + 1 combine
        self.assertEqual(SyncRun.objects.filter(kind=SyncRun.Kind.PROFILES).count(), 1)
        chase = PlayerProfile.objects.get(player__sleeper_id=CHASE)
        self.assertEqual(chase.draft_year, 2021)  # from the ids pass
        self.assertEqual(chase.forty, 4.38)  # from the combine pass

    def test_combine_source_only_skips_draft_pass(self) -> None:
        loader = FakeProfileLoader()
        sync_profiles(loader=loader, sources=COMBINE)
        self.assertIn("fetch_combine", loader.calls)
        self.assertNotIn("fetch_player_ids", loader.calls)


class CoercionTests(SimpleTestCase):
    def test_height_parsing(self) -> None:
        self.assertEqual(_height_to_inches("6-2"), 74)
        self.assertEqual(_height_to_inches("6'2"), 74)
        self.assertEqual(_height_to_inches("74"), 74)
        self.assertIsNone(_height_to_inches(""))
        self.assertIsNone(_height_to_inches("tall"))

    def test_bmi(self) -> None:
        self.assertEqual(_bmi(74, 220), round(220 / 74**2 * 703, 1))
        self.assertIsNone(_bmi(None, 220))
        self.assertIsNone(_bmi(74, None))

    def test_as_float(self) -> None:
        self.assertEqual(_as_float("4.38"), 4.38)
        self.assertIsNone(_as_float(""))
        self.assertIsNone(_as_float("N/A"))  # non-numeric → None
