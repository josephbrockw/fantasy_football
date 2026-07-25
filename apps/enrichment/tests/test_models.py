from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.enrichment.models import PlayerProfile
from apps.players.models import Player


def make_player(sleeper_id: str = "1", name: str = "A Player") -> Player:
    return Player.objects.create(sleeper_id=sleeper_id, full_name=name)


class PlayerProfileTests(TestCase):
    def test_str(self) -> None:
        profile = PlayerProfile(player=make_player(name="Caleb Williams"))
        self.assertEqual(str(profile), "Caleb Williams ( ) profile")

    def test_one_profile_per_player(self) -> None:
        player = make_player()
        PlayerProfile.objects.create(player=player, draft_year=2024)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlayerProfile.objects.create(player=player, draft_year=2024)

    def test_cascade_delete(self) -> None:
        player = make_player()
        PlayerProfile.objects.create(player=player, draft_year=2024)
        player.delete()
        self.assertFalse(PlayerProfile.objects.exists())

    def test_measurables_default_null(self) -> None:
        profile = PlayerProfile.objects.create(
            player=make_player(), draft_year=2024, draft_round=1, draft_pick=1
        )
        profile.refresh_from_db()
        self.assertIsNone(profile.forty)
        self.assertIsNone(profile.bench)
        self.assertIsNone(profile.bmi)

    def test_draft_capital_label(self) -> None:
        profile = PlayerProfile(
            player=make_player(), draft_year=2023, draft_round=1, draft_pick=5
        )
        self.assertEqual(profile.draft_capital_label, "2023 R1.05")

    def test_draft_capital_label_year_only(self) -> None:
        # Draft year known but round/pick missing → just the year.
        profile = PlayerProfile(player=make_player(), draft_year=2020)
        self.assertEqual(profile.draft_capital_label, "2020")

    def test_draft_capital_label_empty_when_undrafted(self) -> None:
        self.assertEqual(PlayerProfile(player=make_player()).draft_capital_label, "")
