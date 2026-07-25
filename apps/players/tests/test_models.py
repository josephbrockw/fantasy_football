from django.test import TestCase

from apps.players.models import Player


class PlayerStrTests(TestCase):
    def test_uses_full_name(self) -> None:
        player = Player(full_name="Ja'Marr Chase", position="WR", team="CIN")
        self.assertEqual(str(player), "Ja'Marr Chase (WR CIN)")

    def test_falls_back_to_name_parts(self) -> None:
        player = Player(
            first_name="Houston", last_name="Texans", position="DEF", team="HOU"
        )
        self.assertEqual(str(player), "Houston Texans (DEF HOU)")

    def test_tolerates_an_empty_player(self) -> None:
        self.assertEqual(str(Player()), "( )")


class IsRookieTests(TestCase):
    def test_true_for_zero_experience(self) -> None:
        self.assertTrue(Player(years_exp=0).is_rookie)

    def test_false_for_veterans(self) -> None:
        self.assertFalse(Player(years_exp=5).is_rookie)

    def test_false_when_unknown(self) -> None:
        self.assertFalse(Player(years_exp=None).is_rookie)
