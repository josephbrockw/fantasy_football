from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.enrichment.models import PlayerProfile
from apps.leagues.models import League, LeagueSeason, RosterSlot, Team
from apps.players import valuation as val
from apps.players.models import Player, PlayerSeasonMetrics, PlayerValue
from apps.sleeper.models import SyncRun

# --- factor unit tests (no DB) ----------------------------------------------


class FactorTests(SimpleTestCase):
    def _metrics(self, ppg=None, recent=None, form_delta=None, games=15):
        return PlayerSeasonMetrics(
            ppg_ppr=ppg,
            recent_ppg_ppr=recent,
            form_delta_ppr=form_delta,
            games_played=games,
        )

    def test_raw_production_none_and_no_ppg(self) -> None:
        self.assertEqual(val.raw_production(None), 0.0)
        self.assertEqual(val.raw_production(self._metrics(ppg=None)), 0.0)

    def test_raw_production_blends_recent_form(self) -> None:
        flat = val.raw_production(self._metrics(ppg=10.0))  # recent falls back to ppg
        up = val.raw_production(self._metrics(ppg=10.0, recent=20.0))
        self.assertEqual(flat, 10.0)
        self.assertGreater(up, flat)  # recent form pulls it up

    def test_durability_factor(self) -> None:
        self.assertEqual(val.durability_factor([]), 1.0)  # no history
        healthy = val.durability_factor([self._metrics(games=17) for _ in range(3)])
        fragile = val.durability_factor([self._metrics(games=10) for _ in range(3)])
        self.assertEqual(healthy, 1.0)
        self.assertEqual(fragile, val.DURABILITY_FLOOR)  # clamped up from 0.59
        self.assertLess(fragile, healthy)

    def test_pedigree_prior_by_round(self) -> None:
        def prof(**kw):
            return PlayerProfile(**kw)

        r1_early = val.pedigree_prior(prof(draft_round=1, draft_pick=3))
        r1_late = val.pedigree_prior(prof(draft_round=1, draft_pick=28))
        r3 = val.pedigree_prior(prof(draft_round=3, draft_pick=80))
        self.assertGreater(r1_early, r1_late)
        self.assertGreater(r1_late, r3)
        self.assertGreater(r3, val.UNDRAFTED_PRIOR)
        self.assertEqual(val.pedigree_prior(None), val.UNDRAFTED_PRIOR)
        self.assertEqual(
            val.pedigree_prior(prof(draft_round=None)), val.UNDRAFTED_PRIOR
        )

    def test_evidence_decay(self) -> None:
        rookie = val.evidence_decay(0, 0)
        veteran = val.evidence_decay(4, 50)
        self.assertAlmostEqual(rookie, 1.0)
        self.assertLess(veteran, 0.5)  # three empty-ish seasons burned the prior
        self.assertTrue(0.0 <= veteran <= 1.0)

    def test_trajectory_nudge_clamped(self) -> None:
        self.assertEqual(val.trajectory_nudge(None), 0.0)
        self.assertEqual(val.trajectory_nudge(self._metrics(form_delta=None)), 0.0)
        huge = val.trajectory_nudge(self._metrics(form_delta=100.0))
        self.assertEqual(huge, val.TRAJECTORY_NUDGE_CAP)

    def test_depth_chart_nudge(self) -> None:
        self.assertEqual(val.depth_chart_nudge(Player(depth_chart_order=None)), 0.0)
        starter = val.depth_chart_nudge(Player(depth_chart_order=1))
        second = val.depth_chart_nudge(Player(depth_chart_order=2))
        third = val.depth_chart_nudge(Player(depth_chart_order=3))
        deep = val.depth_chart_nudge(Player(depth_chart_order=5))
        self.assertEqual(starter, val.DEPTH_CHART_NUDGE_CAP)
        self.assertGreater(starter, second)
        self.assertEqual(third, 0.0)
        self.assertLess(deep, 0.0)
        self.assertGreater(starter, deep)

    def test_market_nudge_is_clamped(self) -> None:
        self.assertEqual(val.market_nudge(0), 0.0)
        self.assertEqual(val.market_nudge(10_000_000), val.MARKET_NUDGE_CAP)
        self.assertGreater(val.market_nudge(5000), 0.0)

    def test_horizon_seasons_curve(self) -> None:
        young_rb = val.horizon_seasons("RB", 23)
        old_rb = val.horizon_seasons("RB", 30)
        old_qb = val.horizon_seasons("QB", 30)
        self.assertGreater(young_rb, old_rb)
        self.assertGreater(old_qb, old_rb)  # QB ages better than RB
        self.assertGreaterEqual(old_rb, 0.0)
        self.assertEqual(val.horizon_seasons("WR", None), val.AGE_CURVES["WR"][1] / 2)

    def test_blend_profiles_sum_to_one(self) -> None:
        for weights in val.WEIGHT_PROFILES.values():
            self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_blend_reweights(self) -> None:
        # A win-now vet (high now, low prospect) vs a prospect (low now, high).
        vet = (90.0, 20.0, 50.0)
        rookie = (20.0, 90.0, 50.0)
        self.assertGreater(val.blend(*vet, "contend"), val.blend(*rookie, "contend"))
        self.assertLess(val.blend(*vet, "rebuild"), val.blend(*rookie, "rebuild"))

    def test_normalize(self) -> None:
        self.assertEqual(val.normalize([]), [])
        self.assertEqual(val.normalize([5.0, 5.0]), [50.0, 50.0])  # all-equal guard
        scaled = val.normalize([0.0, 5.0, 10.0])
        self.assertEqual(scaled[0], 0.0)
        self.assertEqual(scaled[-1], 100.0)

    def test_tier(self) -> None:
        # Percentile within position: the top player is always Tier 1, the
        # bottom lands in the overflow tier, and a lone player is Tier 1.
        self.assertEqual(val._tier(1, 100), 1)  # top of position
        self.assertEqual(val._tier(50, 100), 4)  # 49% ranked above → T4
        self.assertEqual(val._tier(100, 100), len(val.TIER_PERCENTILES) + 1)
        self.assertEqual(val._tier(1, 1), 1)  # thin position, still top


# --- integration (DB) --------------------------------------------------------


def make_player(sid, position="WR", age=25, years_exp=3, depth=None):
    return Player.objects.create(
        sleeper_id=sid,
        full_name=f"Player {sid}",
        position=position,
        age=age,
        years_exp=years_exp,
        depth_chart_order=depth,
    )


def make_metrics(player, season, ppg, games=15, recent=None, form_delta=None):
    return PlayerSeasonMetrics.objects.create(
        player=player,
        season=season,
        position=player.position,
        games_played=games,
        ppg_ppr=ppg,
        recent_ppg_ppr=recent,
        form_delta_ppr=form_delta,
    )


class ComputeTests(TestCase):
    def test_pool_includes_zero_stat_prospect_excludes_stray_vet(self) -> None:
        # A round-1 rookie in the recent draft class, no metrics.
        rookie = make_player("r", position="RB", age=22, years_exp=0)
        PlayerProfile.objects.create(
            player=rookie, draft_year=2024, draft_round=1, draft_pick=5
        )
        # A producer with metrics.
        star = make_player("s", position="WR", age=25, years_exp=4)
        make_metrics(star, 2024, ppg=20.0)
        # An unrostered veteran outside the prospect window, no metrics → no row.
        make_player("stray", position="WR", age=31, years_exp=10)

        val.recompute_values(season=2024)

        self.assertTrue(PlayerValue.objects.filter(player=rookie).exists())
        rookie_value = PlayerValue.objects.get(player=rookie)
        self.assertEqual(rookie_value.now_score, 0.0)  # never played
        self.assertGreater(rookie_value.prospect_score, 0.0)
        self.assertFalse(
            PlayerValue.objects.filter(player__sleeper_id="stray").exists()
        )

    def test_prospect_discriminates_pedigree(self) -> None:
        r1 = make_player("r1", position="WR", age=22, years_exp=0)
        PlayerProfile.objects.create(
            player=r1, draft_year=2024, draft_round=1, draft_pick=4
        )
        udfa = make_player("ud", position="WR", age=22, years_exp=0)
        PlayerProfile.objects.create(player=udfa, draft_year=2024)  # undrafted
        bust = make_player("b", position="WR", age=26, years_exp=4)
        PlayerProfile.objects.create(
            player=bust, draft_year=2020, draft_round=1, draft_pick=6
        )
        make_metrics(bust, 2024, ppg=1.0, games=1)  # empty seasons → prior burned

        val.recompute_values(season=2024)
        r1_score = PlayerValue.objects.get(player=r1).prospect_score
        udfa_score = PlayerValue.objects.get(player=udfa).prospect_score
        bust_score = PlayerValue.objects.get(player=bust).prospect_score
        self.assertGreater(r1_score, udfa_score)  # pedigree matters
        self.assertGreater(r1_score, bust_score)  # decay punishes the bust

    def test_depth_chart_moves_prospect_score(self) -> None:
        starter = make_player("st", position="WR", age=22, years_exp=0, depth=1)
        PlayerProfile.objects.create(
            player=starter, draft_year=2024, draft_round=3, draft_pick=80
        )
        reserve = make_player("re", position="WR", age=22, years_exp=0, depth=4)
        PlayerProfile.objects.create(
            player=reserve, draft_year=2024, draft_round=3, draft_pick=81
        )
        val.recompute_values(season=2024)
        self.assertGreater(
            PlayerValue.objects.get(player=starter).prospect_score,
            PlayerValue.objects.get(player=reserve).prospect_score,
        )

    def test_includes_rostered_players(self) -> None:
        league = League.objects.create(name="L", normalized_name="l", slug="l")
        season = LeagueSeason.objects.create(
            league=league, season="2024", sleeper_league_id="lx"
        )
        team = Team.objects.create(league_season=season, roster_id=1)
        rostered = make_player("ro", position="TE", age=27, years_exp=5)
        RosterSlot.objects.create(team=team, player=rostered)
        # Give someone metrics so the season resolves and pool isn't empty.
        make_metrics(make_player("m"), 2024, ppg=10.0)

        val.recompute_values(season=2024)
        self.assertTrue(PlayerValue.objects.filter(player=rostered).exists())

    def test_orders_by_value_and_ranks_per_position(self) -> None:
        wr1 = make_player("w1", position="WR")
        wr2 = make_player("w2", position="WR")
        rb1 = make_player("b1", position="RB")
        make_metrics(wr1, 2024, ppg=25.0)
        make_metrics(wr2, 2024, ppg=10.0)
        make_metrics(rb1, 2024, ppg=18.0)

        val.recompute_values(season=2024)
        top = PlayerValue.objects.get(player=wr1)
        self.assertEqual(top.overall_rank, 1)
        self.assertEqual(top.position_rank, 1)
        self.assertEqual(PlayerValue.objects.get(player=wr2).position_rank, 2)
        rb_value = PlayerValue.objects.get(player=rb1)
        self.assertEqual(rb_value.position_rank, 1)  # ranks within its own position
        # components carry every factor for inspection.
        self.assertIn("pedigree_prior", top.components)
        self.assertIn("now_score", top.components)

    def test_recompute_is_idempotent(self) -> None:
        player = make_player("p")
        make_metrics(player, 2024, ppg=15.0)
        val.recompute_values(season=2024)
        first = PlayerValue.objects.get(player=player, season=2024)

        val.recompute_values(season=2024)  # re-run; metrics already present
        rows = PlayerValue.objects.filter(player=player, season=2024)
        self.assertEqual(rows.count(), 1)  # upsert, no dupes
        self.assertGreater(rows.get().updated_at, first.updated_at)

    def test_defaults_to_latest_season(self) -> None:
        player = make_player("p")
        make_metrics(player, 2023, ppg=10.0)
        make_metrics(player, 2025, ppg=12.0)
        val.recompute_values()  # no season → latest (2025)
        self.assertTrue(PlayerValue.objects.filter(season=2025).exists())
        self.assertFalse(PlayerValue.objects.filter(season=2023).exists())

    def test_wraps_in_syncrun(self) -> None:
        make_metrics(make_player("p"), 2024, ppg=10.0)
        val.recompute_values(season=2024)
        run = SyncRun.objects.get(kind=SyncRun.Kind.VALUATION)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertGreater(run.records_written, 0)

    def test_failure_recorded(self) -> None:
        make_metrics(make_player("p"), 2024, ppg=10.0)
        boom = mock.Mock(side_effect=RuntimeError("boom"))
        with (
            mock.patch.dict(val.VALUATION_MODELS, {"baseline-v1": boom}),
            self.assertRaises(RuntimeError),
        ):
            val.recompute_values(season=2024)
        run = SyncRun.objects.get(kind=SyncRun.Kind.VALUATION)
        self.assertEqual(run.status, SyncRun.Status.FAILED)

    def test_dry_run_writes_nothing(self) -> None:
        make_metrics(make_player("p"), 2024, ppg=10.0)
        stats = val.recompute_values(season=2024, dry_run=True)
        self.assertEqual(PlayerValue.objects.count(), 0)
        self.assertGreater(stats.written, 0)

    def test_unknown_model_version(self) -> None:
        with self.assertRaises(ValueError):
            val.recompute_values(season=2024, model_version="nope")

    def test_no_metrics_raises(self) -> None:
        with self.assertRaises(ValueError):
            val.recompute_values()  # nothing to value

    def test_empty_pool_writes_nothing(self) -> None:
        # A season with no metrics / rosters / draft class → empty pool, no rows.
        stats = val.recompute_values(season=2099)
        self.assertEqual(stats.written, 0)
        self.assertFalse(PlayerValue.objects.exists())
