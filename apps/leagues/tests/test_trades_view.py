from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.leagues.models import (
    League,
    LeagueSeason,
    Manager,
    Team,
    Trade,
    TradeAsset,
    TradedPick,
)
from apps.players.models import Player


class TradesViewFixture(TestCase):
    league: League
    season: LeagueSeason
    old_season: LeagueSeason
    me: Manager
    rival: Manager
    my_team: Team
    rival_team: Team
    player: Player
    trade: Trade

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="The Dynasty", normalized_name="thedynasty", slug="the-dynasty"
        )
        cls.season = LeagueSeason.objects.create(
            league=cls.league, season="2026", sleeper_league_id="l1"
        )
        cls.old_season = LeagueSeason.objects.create(
            league=cls.league, season="2025", sleeper_league_id="l0"
        )
        cls.me = Manager.objects.create(
            sleeper_user_id="me", display_name="Me", is_me=True
        )
        cls.rival = Manager.objects.create(sleeper_user_id="rv", display_name="Rival")
        cls.my_team = Team.objects.create(
            league_season=cls.season, roster_id=1, manager=cls.me
        )
        cls.rival_team = Team.objects.create(
            league_season=cls.season, roster_id=2, manager=cls.rival
        )
        cls.player = Player.objects.create(
            sleeper_id="pa", full_name="Traded Guy", position="WR", team="CIN"
        )
        cls.trade = Trade.objects.create(
            league_season=cls.season,
            sleeper_transaction_id="t1",
            week=3,
            status="complete",
            status_updated=timezone.now(),
        )
        # I receive the player; the rival receives a pick and FAAB.
        TradeAsset.objects.create(
            trade=cls.trade,
            kind=TradeAsset.Kind.PLAYER,
            player=cls.player,
            from_team=cls.rival_team,
            to_team=cls.my_team,
        )
        TradeAsset.objects.create(
            trade=cls.trade,
            kind=TradeAsset.Kind.PICK,
            pick_season="2027",
            pick_round=1,
            from_team=cls.my_team,
            to_team=cls.rival_team,
        )
        TradeAsset.objects.create(
            trade=cls.trade,
            kind=TradeAsset.Kind.FAAB,
            faab_amount=15,
            from_team=cls.my_team,
            to_team=cls.rival_team,
        )
        TradedPick.objects.create(
            league_season=cls.season,
            season="2027",
            round=1,
            original_owner=cls.rival,
            current_owner=cls.me,
        )

    def url(self, **params: str) -> str:
        base = reverse("leagues:trades", args=[self.league.slug])
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            return f"{base}?{query}"
        return base


class TradesViewTests(TradesViewFixture):
    def test_page_renders_with_both_managers(self) -> None:
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Traded Guy")
        self.assertContains(response, "Me")
        self.assertContains(response, "Rival")

    def test_all_asset_kinds_render(self) -> None:
        response = self.client.get(self.url())
        self.assertContains(response, "Traded Guy")  # player
        self.assertContains(response, "2027 R1 pick")  # pick label
        self.assertContains(response, "$15 FAAB")  # faab label

    def test_mine_marker_on_received_assets(self) -> None:
        response = self.client.get(self.url())
        self.assertContains(response, "me</span>")  # the "me" badge

    def test_pick_ownership_table(self) -> None:
        response = self.client.get(self.url())
        self.assertContains(response, "2027 R1")
        # originally the rival's, now mine
        self.assertContains(response, "Rival")
        self.assertContains(response, "Me")

    def test_season_picker_defaults_to_newest(self) -> None:
        response = self.client.get(self.url())
        self.assertEqual(response.context["season"], self.season)

    def test_season_picker_switches(self) -> None:
        response = self.client.get(self.url(season="2025"))
        self.assertEqual(response.context["season"], self.old_season)
        self.assertNotContains(response, "Traded Guy")  # the trade is in 2026

    def test_empty_states(self) -> None:
        # 2025 has no trades and no traded picks.
        response = self.client.get(self.url(season="2025"))
        self.assertContains(response, "No trades recorded for this season")
        self.assertContains(response, "No picks have changed hands")

    def test_subnav_has_trades_link(self) -> None:
        response = self.client.get(self.url())
        link = reverse("leagues:trades", args=[self.league.slug])
        self.assertContains(response, link)

    def test_league_with_no_seasons_degrades(self) -> None:
        League.objects.create(name="Empty", normalized_name="empty", slug="empty")
        response = self.client.get(reverse("leagues:trades", args=["empty"]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["season"])
        self.assertEqual(list(response.context["trades"]), [])
        self.assertEqual(list(response.context["traded_picks"]), [])
        self.assertContains(response, "No seasons synced")

    def test_query_budget(self) -> None:
        # prefetch_related keeps this flat regardless of trade/asset count:
        # league, seasons, trades, assets, player, from_team(+mgr), to_team(+mgr),
        # and the traded-picks join.
        with self.assertNumQueries(10):
            self.client.get(self.url())
