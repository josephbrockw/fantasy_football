from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
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


class TradeModelFixture(TestCase):
    league: League
    season: LeagueSeason
    me: Manager
    rival: Manager
    my_team: Team
    rival_team: Team
    player: Player

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="The Dynasty", normalized_name="thedynasty", slug="the-dynasty"
        )
        cls.season = LeagueSeason.objects.create(
            league=cls.league, season="2026", sleeper_league_id="l1"
        )
        cls.me = Manager.objects.create(sleeper_user_id="me", display_name="Me")
        cls.rival = Manager.objects.create(sleeper_user_id="rv", display_name="Rival")
        cls.my_team = Team.objects.create(
            league_season=cls.season, roster_id=1, manager=cls.me
        )
        cls.rival_team = Team.objects.create(
            league_season=cls.season, roster_id=2, manager=cls.rival
        )
        cls.player = Player.objects.create(sleeper_id="p1", full_name="Traded Guy")

    def make_trade(self, txn_id: str, **fields: Any) -> Trade:
        defaults: dict[str, Any] = {"week": 3, "status": "complete"}
        defaults.update(fields)
        return Trade.objects.create(
            league_season=self.season, sleeper_transaction_id=txn_id, **defaults
        )


class TradeTests(TradeModelFixture):
    def test_str_and_ordering_newest_first(self) -> None:
        now = timezone.now()
        older = self.make_trade("t1", status_updated=now - timedelta(hours=1))
        newer = self.make_trade("t2", status_updated=now)
        self.assertEqual(str(newer), "Trade t2 (wk 3)")
        self.assertEqual(list(Trade.objects.all()), [newer, older])

    def test_unique_transaction_id(self) -> None:
        self.make_trade("dup")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_trade("dup")


class TradeAssetTests(TradeModelFixture):
    def test_label_per_kind(self) -> None:
        trade = self.make_trade("t1")
        player_asset = TradeAsset.objects.create(
            trade=trade,
            kind=TradeAsset.Kind.PLAYER,
            player=self.player,
            from_team=self.rival_team,
            to_team=self.my_team,
        )
        pick_asset = TradeAsset.objects.create(
            trade=trade,
            kind=TradeAsset.Kind.PICK,
            pick_season="2027",
            pick_round=1,
            from_team=self.my_team,
            to_team=self.rival_team,
        )
        faab_asset = TradeAsset.objects.create(
            trade=trade,
            kind=TradeAsset.Kind.FAAB,
            faab_amount=15,
            from_team=self.my_team,
            to_team=self.rival_team,
        )
        self.assertIn("Traded Guy", player_asset.label)
        self.assertEqual(pick_asset.label, "2027 R1 pick")
        self.assertEqual(faab_asset.label, "$15 FAAB")
        # __str__ delegates to label.
        self.assertEqual(str(pick_asset), "2027 R1 pick")

    def test_label_unknown_kind_is_dash(self) -> None:
        # Defensive default for a kind outside the enum.
        self.assertEqual(TradeAsset(kind="mystery").label, "—")

    def test_player_is_protected_from_delete(self) -> None:
        trade = self.make_trade("t1")
        TradeAsset.objects.create(
            trade=trade, kind=TradeAsset.Kind.PLAYER, player=self.player
        )
        with self.assertRaises(ProtectedError):
            self.player.delete()
        self.assertTrue(TradeAsset.objects.exists())


class TradedPickTests(TradeModelFixture):
    def make_pick(self, **fields: Any) -> TradedPick:
        defaults: dict[str, Any] = {
            "league_season": self.season,
            "season": "2027",
            "round": 1,
            "original_owner": self.rival,
            "current_owner": self.me,
        }
        defaults.update(fields)
        return TradedPick.objects.create(**defaults)

    def test_str(self) -> None:
        self.assertEqual(str(self.make_pick()), "2027 R1 pick")

    def test_unique_together(self) -> None:
        self.make_pick()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_pick()

    def test_same_round_different_original_owner_allowed(self) -> None:
        self.make_pick(original_owner=self.rival)
        self.make_pick(original_owner=self.me)
        self.assertEqual(TradedPick.objects.count(), 2)

    def test_owners_are_managers_with_reverse_relations(self) -> None:
        pick = self.make_pick(original_owner=self.rival, current_owner=self.me)
        self.assertEqual(pick.original_owner, self.rival)
        self.assertEqual(pick.current_owner, self.me)
        # Reverse relations (used by the PR 03 view) resolve to this pick.
        self.assertIn(pick, self.rival.picks_originally_owned.all())
        self.assertIn(pick, self.me.picks_owned.all())
