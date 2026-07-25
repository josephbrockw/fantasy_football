from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.players.models import Player, PlayerSeasonMetrics


def make_player(sleeper_id: str = "1", name: str = "A Player") -> Player:
    return Player.objects.create(sleeper_id=sleeper_id, full_name=name)


class PlayerSeasonMetricsTests(TestCase):
    def test_str(self) -> None:
        metrics = PlayerSeasonMetrics(
            player=make_player(name="Ja'Marr Chase"), season=2024
        )
        self.assertIn("Ja'Marr Chase", str(metrics))
        self.assertIn("2024 metrics", str(metrics))

    def test_unique_together_enforced(self) -> None:
        player = make_player()
        PlayerSeasonMetrics.objects.create(player=player, season=2024)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlayerSeasonMetrics.objects.create(player=player, season=2024)

    def test_same_season_different_type_allowed(self) -> None:
        player = make_player()
        PlayerSeasonMetrics.objects.create(
            player=player, season=2024, season_type="regular"
        )
        PlayerSeasonMetrics.objects.create(
            player=player, season=2024, season_type="post"
        )
        self.assertEqual(PlayerSeasonMetrics.objects.filter(player=player).count(), 2)

    def test_default_ordering_newest_first(self) -> None:
        player = make_player()
        older = PlayerSeasonMetrics.objects.create(player=player, season=2023)
        newer = PlayerSeasonMetrics.objects.create(player=player, season=2024)
        self.assertEqual(list(PlayerSeasonMetrics.objects.all()), [newer, older])

    def test_cascade_delete(self) -> None:
        player = make_player()
        PlayerSeasonMetrics.objects.create(player=player, season=2024)
        player.delete()
        self.assertFalse(PlayerSeasonMetrics.objects.exists())

    def test_usage_jsonfield_roundtrips(self) -> None:
        player = make_player()
        payload = {"rec_tgt": 120, "rush_att": 15, "nested": {"snaps": 900}}
        metrics = PlayerSeasonMetrics.objects.create(
            player=player, season=2024, usage=payload
        )
        metrics.refresh_from_db()
        self.assertEqual(metrics.usage, payload)

    def test_usage_defaults_to_empty_dict(self) -> None:
        metrics = PlayerSeasonMetrics.objects.create(player=make_player(), season=2024)
        metrics.refresh_from_db()
        self.assertEqual(metrics.usage, {})

    def test_nullable_measures_default_to_none(self) -> None:
        metrics = PlayerSeasonMetrics.objects.create(player=make_player(), season=2024)
        metrics.refresh_from_db()
        self.assertIsNone(metrics.ppg_ppr)
        self.assertIsNone(metrics.stdev_ppr)
        self.assertIsNone(metrics.targets)
        self.assertEqual(metrics.games_played, 0)
