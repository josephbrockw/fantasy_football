from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.players.models import Player, PlayerValue


def make_player(sleeper_id: str = "1", name: str = "A Player") -> Player:
    return Player.objects.create(sleeper_id=sleeper_id, full_name=name)


class PlayerValueModelTests(TestCase):
    def test_natural_key_is_unique(self) -> None:
        player = make_player()
        PlayerValue.objects.create(player=player, season=2024, value=50.0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlayerValue.objects.create(player=player, season=2024, value=60.0)

    def test_same_player_different_version_or_season_allowed(self) -> None:
        player = make_player()
        PlayerValue.objects.create(player=player, season=2024, value=50.0)
        PlayerValue.objects.create(
            player=player, season=2024, model_version="trained-v1", value=55.0
        )
        PlayerValue.objects.create(player=player, season=2023, value=45.0)
        self.assertEqual(PlayerValue.objects.filter(player=player).count(), 3)

    def test_defaults(self) -> None:
        value = PlayerValue.objects.create(
            player=make_player(), season=2024, value=50.0
        )
        value.refresh_from_db()
        self.assertEqual(value.model_version, "baseline-v1")
        self.assertEqual(value.components, {})
        self.assertEqual(value.now_score, 0.0)
        self.assertEqual(value.prospect_score, 0.0)
        self.assertEqual(value.horizon_score, 0.0)
        self.assertEqual(value.horizon_seasons, 0.0)
        self.assertIsNone(value.tier)
        self.assertIsNone(value.position_rank)
        self.assertIsNone(value.expires_season)

    def test_sub_scores_roundtrip(self) -> None:
        value = PlayerValue.objects.create(
            player=make_player(),
            season=2024,
            value=72.5,
            now_score=80.0,
            prospect_score=40.0,
            horizon_score=90.0,
            horizon_seasons=4.5,
            expires_season=2028,
        )
        value.refresh_from_db()
        self.assertEqual(value.now_score, 80.0)
        self.assertEqual(value.prospect_score, 40.0)
        self.assertEqual(value.horizon_score, 90.0)
        self.assertEqual(value.horizon_seasons, 4.5)
        self.assertEqual(value.expires_season, 2028)

    def test_components_roundtrips(self) -> None:
        payload = {"production": 18.2, "age_multiplier": 0.9, "raw": {"now": 82.0}}
        value = PlayerValue.objects.create(
            player=make_player(), season=2024, value=50.0, components=payload
        )
        value.refresh_from_db()
        self.assertEqual(value.components, payload)

    def test_str(self) -> None:
        value = PlayerValue(
            player=make_player(name="Ja'Marr Chase"), season=2024, value=88.4
        )
        text = str(value)
        self.assertIn("Ja'Marr Chase", text)
        self.assertIn("2024", text)
        self.assertIn("baseline-v1", text)
        self.assertIn("88.4", text)

    def test_ordering_newest_then_highest_value(self) -> None:
        player = make_player()
        old = PlayerValue.objects.create(player=player, season=2023, value=90.0)
        low = PlayerValue.objects.create(player=player, season=2024, value=40.0)
        high_p = make_player(sleeper_id="2", name="Other")
        high = PlayerValue.objects.create(player=high_p, season=2024, value=80.0)
        # 2024 rows first (newest), highest value first within the season.
        self.assertEqual(list(PlayerValue.objects.all()), [high, low, old])
