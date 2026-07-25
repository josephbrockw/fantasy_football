from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.players.models import Player, PlayerWeekStat


def make_player(sleeper_id: str = "1", name: str = "Player A") -> Player:
    return Player.objects.create(sleeper_id=sleeper_id, full_name=name)


class PlayerWeekStatTests(TestCase):
    def test_str(self) -> None:
        stat = PlayerWeekStat(
            player=make_player(name="Ja'Marr Chase"),
            season=2025,
            week=3,
            kind=PlayerWeekStat.Kind.STAT,
        )
        self.assertIn("Ja'Marr Chase", str(stat))
        self.assertIn("2025 W3 stat", str(stat))

    def test_unique_together_enforced(self) -> None:
        player = make_player()
        PlayerWeekStat.objects.create(player=player, season=2025, week=1, kind="stat")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlayerWeekStat.objects.create(
                player=player, season=2025, week=1, kind="stat"
            )

    def test_stat_and_projection_coexist_for_same_week(self) -> None:
        player = make_player()
        PlayerWeekStat.objects.create(player=player, season=2025, week=1, kind="stat")
        PlayerWeekStat.objects.create(
            player=player, season=2025, week=1, kind="projection"
        )
        self.assertEqual(PlayerWeekStat.objects.count(), 2)

    def test_default_ordering_newest_first(self) -> None:
        player = make_player()
        older = PlayerWeekStat.objects.create(
            player=player, season=2024, week=1, kind="stat"
        )
        newer = PlayerWeekStat.objects.create(
            player=player, season=2025, week=1, kind="stat"
        )
        self.assertEqual(list(PlayerWeekStat.objects.all()), [newer, older])

    def test_deleting_player_cascades(self) -> None:
        player = make_player()
        PlayerWeekStat.objects.create(player=player, season=2025, week=1, kind="stat")
        player.delete()
        self.assertFalse(PlayerWeekStat.objects.exists())

    def test_stats_jsonfield_roundtrips(self) -> None:
        player = make_player()
        payload = {"pass_yd": 305, "pass_td": 3, "nested": {"rush_yd": 12}}
        stat = PlayerWeekStat.objects.create(
            player=player, season=2025, week=1, kind="stat", stats=payload
        )
        stat.refresh_from_db()
        self.assertEqual(stat.stats, payload)

    def test_stats_defaults_to_empty_dict(self) -> None:
        player = make_player()
        stat = PlayerWeekStat.objects.create(
            player=player, season=2025, week=1, kind="stat"
        )
        stat.refresh_from_db()
        self.assertEqual(stat.stats, {})
