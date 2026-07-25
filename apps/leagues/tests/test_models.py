from django.test import TestCase

from apps.leagues.models import (
    League,
    LeagueSeason,
    Manager,
    RosterSlot,
    SleeperAccount,
    Team,
)
from apps.players.models import Player


class StrTests(TestCase):
    def test_sleeper_account(self) -> None:
        account = SleeperAccount(username="dynastyguy", sleeper_user_id="u1")
        self.assertEqual(str(account), "dynastyguy (u1)")

    def test_league(self) -> None:
        self.assertEqual(str(League(name="The Dynasty")), "The Dynasty")

    def test_league_season(self) -> None:
        league = League.objects.create(
            name="The Dynasty", normalized_name="thedynasty", slug="the-dynasty"
        )
        season = LeagueSeason.objects.create(
            league=league, season="2026", sleeper_league_id="l1"
        )
        self.assertEqual(str(season), "The Dynasty 2026")

    def test_manager_prefers_display_name(self) -> None:
        self.assertEqual(
            str(Manager(sleeper_user_id="u1", username="rival", display_name="Rival")),
            "Rival",
        )

    def test_manager_falls_back_to_username_then_id(self) -> None:
        self.assertEqual(str(Manager(sleeper_user_id="u1", username="rival")), "rival")
        self.assertEqual(str(Manager(sleeper_user_id="u1")), "u1")

    def test_team_prefers_team_name_then_manager(self) -> None:
        manager = Manager(sleeper_user_id="u1", display_name="Rival")
        self.assertEqual(str(Team(roster_id=3, team_name="My Squad")), "My Squad")
        self.assertEqual(str(Team(roster_id=3, manager=manager)), "Rival")

    def test_roster_slot(self) -> None:
        league = League.objects.create(name="L", normalized_name="l", slug="l")
        season = LeagueSeason.objects.create(
            league=league, season="2026", sleeper_league_id="l1"
        )
        team = Team.objects.create(league_season=season, roster_id=1)
        player = Player.objects.create(sleeper_id="p1", full_name="A Player")
        slot = RosterSlot.objects.create(
            team=team, player=player, slot=RosterSlot.Slot.STARTER
        )
        self.assertIn("A Player", str(slot))
        self.assertIn("starter", str(slot))

    def test_roster_slot_prefers_the_lineup_position(self) -> None:
        """A starter reads as its lineup slot, not the generic "starter"."""
        league = League.objects.create(name="L2", normalized_name="l2", slug="l2")
        season = LeagueSeason.objects.create(
            league=league, season="2026", sleeper_league_id="l2-1"
        )
        team = Team.objects.create(league_season=season, roster_id=1)
        player = Player.objects.create(sleeper_id="p2", full_name="Superflex Guy")
        slot = RosterSlot.objects.create(
            team=team,
            player=player,
            slot=RosterSlot.Slot.STARTER,
            lineup_position="SUPER_FLEX",
            lineup_order=8,
        )
        self.assertIn("SUPER_FLEX", str(slot))
        self.assertNotIn("starter", str(slot))


class CurrentSeasonTests(TestCase):
    def test_returns_none_when_no_seasons(self) -> None:
        league = League.objects.create(name="L", normalized_name="l", slug="l")
        self.assertIsNone(league.current_season)

    def test_returns_the_newest_season(self) -> None:
        league = League.objects.create(name="L", normalized_name="l", slug="l")
        LeagueSeason.objects.create(league=league, season="2025", sleeper_league_id="a")
        newest = LeagueSeason.objects.create(
            league=league, season="2027", sleeper_league_id="b"
        )
        self.assertEqual(league.current_season, newest)
