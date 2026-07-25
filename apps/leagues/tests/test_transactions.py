from __future__ import annotations

from typing import Any

from django.test import TestCase

from apps.leagues.models import (
    League,
    LeagueSeason,
    Manager,
    Team,
    Trade,
    TradeAsset,
    TradedPick,
)
from apps.leagues.tests.factories import (
    FakeLeagueClient,
    make_non_trade,
    make_trade,
    make_traded_pick,
)
from apps.leagues.transactions import sync_transactions
from apps.players.models import Player
from apps.sleeper.client import SleeperAPIError
from apps.sleeper.models import SyncRun

LEAGUE_ID = "l1"


class RaisingTransactionClient(FakeLeagueClient):
    """A fake whose transaction fetch fails, to exercise the failure path."""

    def get_league_transactions(self, league_id: str, week: int) -> list:
        raise SleeperAPIError("boom")


class TransactionSyncFixture(TestCase):
    league: League
    season: LeagueSeason
    me: Manager
    rival: Manager
    my_team: Team
    rival_team: Team
    player_a: Player

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="The Dynasty", normalized_name="thedynasty", slug="the-dynasty"
        )
        cls.season = LeagueSeason.objects.create(
            league=cls.league, season="2026", sleeper_league_id=LEAGUE_ID
        )
        cls.me = Manager.objects.create(sleeper_user_id="me", display_name="Me")
        cls.rival = Manager.objects.create(sleeper_user_id="rv", display_name="Rival")
        cls.my_team = Team.objects.create(
            league_season=cls.season, roster_id=1, manager=cls.me
        )
        cls.rival_team = Team.objects.create(
            league_season=cls.season, roster_id=2, manager=cls.rival
        )
        cls.player_a = Player.objects.create(sleeper_id="pa", full_name="Player A")

    def client_with(
        self,
        *,
        transactions: dict | None = None,
        traded_picks: dict | None = None,
    ) -> FakeLeagueClient:
        return FakeLeagueClient(
            transactions=transactions or {},
            traded_picks=traded_picks or {},
        )

    def trade_week1(self, *trades: dict) -> dict:
        return {(LEAGUE_ID, 1): list(trades)}

    def picks_payload(self, **fields: Any) -> dict:
        return {LEAGUE_ID: [make_traded_pick(**fields)]}


class TradeSyncTests(TransactionSyncFixture):
    def test_only_trades_ingested(self) -> None:
        txns = self.trade_week1(
            make_trade("t1", adds={"pa": 1}, drops={"pa": 2}),
            make_non_trade("w1", "waiver"),
            make_non_trade("f1", "free_agent"),
        )
        sync_transactions(client=self.client_with(transactions=txns))
        self.assertEqual(Trade.objects.count(), 1)
        self.assertEqual(Trade.objects.get().sleeper_transaction_id, "t1")

    def test_non_complete_trade_skipped(self) -> None:
        txns = self.trade_week1(
            make_trade("t1", status="failed", adds={"pa": 1}, drops={"pa": 2})
        )
        sync_transactions(client=self.client_with(transactions=txns))
        self.assertFalse(Trade.objects.exists())

    def test_player_assets_record_direction(self) -> None:
        txns = self.trade_week1(make_trade("t1", adds={"pa": 1}, drops={"pa": 2}))
        sync_transactions(client=self.client_with(transactions=txns))
        asset = TradeAsset.objects.get(kind=TradeAsset.Kind.PLAYER)
        self.assertEqual(asset.player, self.player_a)
        self.assertEqual(asset.to_team, self.my_team)  # roster 1 received
        self.assertEqual(asset.from_team, self.rival_team)  # roster 2 sent

    def test_pick_assets(self) -> None:
        picks = [
            {
                "season": "2027",
                "round": 1,
                "roster_id": 2,
                "previous_owner_id": 2,
                "owner_id": 1,
            }
        ]
        txns = self.trade_week1(make_trade("t1", draft_picks=picks))
        sync_transactions(client=self.client_with(transactions=txns))
        asset = TradeAsset.objects.get(kind=TradeAsset.Kind.PICK)
        self.assertEqual(asset.pick_season, "2027")
        self.assertEqual(asset.pick_round, 1)
        self.assertEqual(asset.from_team, self.rival_team)  # previous_owner_id 2
        self.assertEqual(asset.to_team, self.my_team)  # owner_id 1

    def test_faab_assets(self) -> None:
        budget = [{"sender": 2, "receiver": 1, "amount": 15}]
        txns = self.trade_week1(make_trade("t1", waiver_budget=budget))
        sync_transactions(client=self.client_with(transactions=txns))
        asset = TradeAsset.objects.get(kind=TradeAsset.Kind.FAAB)
        self.assertEqual(asset.faab_amount, 15)
        self.assertEqual(asset.from_team, self.rival_team)
        self.assertEqual(asset.to_team, self.my_team)

    def test_missing_traded_player_skipped(self) -> None:
        txns = self.trade_week1(make_trade("t1", adds={"ghost": 1}, drops={"ghost": 2}))
        stats = sync_transactions(client=self.client_with(transactions=txns))
        self.assertEqual(Trade.objects.count(), 1)  # the trade still records
        self.assertFalse(TradeAsset.objects.filter(kind="player").exists())
        self.assertEqual(stats.skipped, 1)

    def test_upsert_is_idempotent(self) -> None:
        picks = [
            {
                "season": "2027",
                "round": 1,
                "roster_id": 2,
                "previous_owner_id": 2,
                "owner_id": 1,
            }
        ]
        txns = self.trade_week1(
            make_trade("t1", adds={"pa": 1}, drops={"pa": 2}, draft_picks=picks)
        )
        sync_transactions(client=self.client_with(transactions=txns))
        sync_transactions(client=self.client_with(transactions=txns))
        self.assertEqual(Trade.objects.count(), 1)
        self.assertEqual(TradeAsset.objects.count(), 2)  # rebuilt, not doubled

    def test_status_updated_converted_from_epoch_ms(self) -> None:
        txns = self.trade_week1(
            make_trade(
                "t1", status_updated=1_700_000_000_000, adds={"pa": 1}, drops={"pa": 2}
            )
        )
        sync_transactions(client=self.client_with(transactions=txns))
        trade = Trade.objects.get()
        assert trade.status_updated is not None
        self.assertEqual(trade.status_updated.year, 2023)

    def test_season_filter_limits_scope(self) -> None:
        txns = self.trade_week1(make_trade("t1", adds={"pa": 1}, drops={"pa": 2}))
        client = self.client_with(transactions=txns)
        sync_transactions(client=client, season="1999")  # no season matches
        self.assertFalse(Trade.objects.exists())
        sync_transactions(client=client, season="2026")  # matches
        self.assertTrue(Trade.objects.exists())

    def test_null_status_updated_tolerated(self) -> None:
        txns = self.trade_week1(
            make_trade("t1", status_updated=None, adds={"pa": 1}, drops={"pa": 2})
        )
        sync_transactions(client=self.client_with(transactions=txns))
        self.assertIsNone(Trade.objects.get().status_updated)


class TradedPickSyncTests(TransactionSyncFixture):
    def test_picks_map_to_manager_owners(self) -> None:
        picks = self.picks_payload(season="2027", round=1, roster_id=2, owner_id=1)
        sync_transactions(client=self.client_with(traded_picks=picks))
        pick = TradedPick.objects.get()
        self.assertEqual(pick.original_owner, self.rival)  # roster 2
        self.assertEqual(pick.current_owner, self.me)  # owner 1

    def test_rebuilt_wholesale_without_duplicates(self) -> None:
        first = self.picks_payload(season="2027", round=1, roster_id=2, owner_id=1)
        sync_transactions(client=self.client_with(traded_picks=first))
        second = self.picks_payload(season="2027", round=1, roster_id=2, owner_id=2)
        sync_transactions(client=self.client_with(traded_picks=second))
        self.assertEqual(TradedPick.objects.count(), 1)
        self.assertEqual(TradedPick.objects.get().current_owner, self.rival)

    def test_future_season_pick_stored(self) -> None:
        picks = self.picks_payload(season="2099", round=2, roster_id=1, owner_id=2)
        sync_transactions(client=self.client_with(traded_picks=picks))
        pick = TradedPick.objects.get()
        self.assertEqual(pick.season, "2099")
        self.assertFalse(LeagueSeason.objects.filter(season="2099").exists())
        self.assertEqual(pick.original_owner, self.me)
        self.assertEqual(pick.current_owner, self.rival)

    def test_orphan_roster_pick_skipped(self) -> None:
        picks = self.picks_payload(season="2027", round=1, roster_id=99, owner_id=1)
        stats = sync_transactions(client=self.client_with(traded_picks=picks))
        self.assertFalse(TradedPick.objects.exists())
        self.assertEqual(stats.skipped, 1)


class SyncRunTests(TransactionSyncFixture):
    def test_success_recorded(self) -> None:
        txns = self.trade_week1(make_trade("t1", adds={"pa": 1}, drops={"pa": 2}))
        sync_transactions(client=self.client_with(transactions=txns))
        run = SyncRun.objects.get(kind=SyncRun.Kind.TRANSACTIONS)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertGreater(run.records_written, 0)

    def test_failure_recorded_and_reraised(self) -> None:
        with self.assertRaises(SleeperAPIError):
            sync_transactions(client=RaisingTransactionClient())
        run = SyncRun.objects.get(kind=SyncRun.Kind.TRANSACTIONS)
        self.assertEqual(run.status, SyncRun.Status.FAILED)
