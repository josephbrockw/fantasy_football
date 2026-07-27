from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.leagues.models import League, LeagueSeason, Manager, RosterSlot, Team
from apps.players.models import Player, PlayerValue
from apps.scouting.models import ScoutingNote, Target
from apps.scouting.tests.test_models import make_player


def flatten(groups: list[dict]) -> list[Player]:
    return [player for group in groups for player in group["players"]]


def make_value(
    player: Player,
    *,
    now: float = 0.0,
    prospect: float = 0.0,
    horizon: float = 0.0,
    value: float | None = None,
    tier: int | None = None,
    season: int = 2026,
) -> PlayerValue:
    return PlayerValue.objects.create(
        player=player,
        season=season,
        position=player.position,
        now_score=now,
        prospect_score=prospect,
        horizon_score=horizon,
        value=value if value is not None else now,
        tier=tier,
    )


class ScoutingFixture(TestCase):
    league: League
    season: LeagueSeason
    me: Manager
    rival: Manager
    my_team: Team
    rival_team: Team
    qb: Player
    wr: Player
    rb: Player
    veteran: Player
    rival_star: Player
    my_star: Player

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="The Dynasty", slug="the-dynasty", normalized_name="thedynasty"
        )
        cls.season = LeagueSeason.objects.create(
            league=cls.league,
            season="2026",
            sleeper_league_id="S1",
            roster_positions=["QB", "RB", "WR", "TE", "BN"],
        )
        cls.me = Manager.objects.create(
            sleeper_user_id="me", display_name="Me", is_me=True
        )
        cls.rival = Manager.objects.create(
            sleeper_user_id="rv", display_name="Rival", is_me=False
        )
        cls.my_team = Team.objects.create(
            league_season=cls.season, roster_id=1, manager=cls.me, team_name="My Squad"
        )
        cls.rival_team = Team.objects.create(
            league_season=cls.season, roster_id=2, manager=cls.rival, team_name="Rivals"
        )
        cls.qb = make_player("1", "Caleb Williams", position="QB", age=22)
        cls.wr = make_player("2", "Marvin Harrison", position="WR", age=21)
        cls.rb = make_player("3", "Blake Corum", position="RB", age=23)
        cls.veteran = make_player("9", "Patrick Mahomes", position="QB", years_exp=7)
        cls.rival_star = make_player("10", "Rival Star", position="WR", years_exp=4)
        cls.my_star = make_player("11", "My Star", position="RB", years_exp=3)
        RosterSlot.objects.create(team=cls.rival_team, player=cls.rival_star)
        RosterSlot.objects.create(team=cls.my_team, player=cls.my_star)


class RookieBoardTests(ScoutingFixture):
    def url(self) -> str:
        return reverse("scouting:rookie_board", args=[self.league.slug])

    def test_lists_only_rookies(self) -> None:
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        players = flatten(response.context["groups"])
        self.assertIn(self.qb, players)
        self.assertNotIn(self.veteran, players)
        self.assertContains(response, "Caleb Williams")
        self.assertNotContains(response, "Patrick Mahomes")

    def test_grouped_by_position_in_football_order(self) -> None:
        keys = [g["key"] for g in self.client.get(self.url()).context["groups"]]
        self.assertEqual(keys, ["QB", "RB", "WR"])

    def test_position_filter(self) -> None:
        response = self.client.get(self.url(), {"pos": "WR"})
        self.assertEqual(flatten(response.context["groups"]), [self.wr])

    def test_search_filter_is_case_insensitive(self) -> None:
        response = self.client.get(self.url(), {"q": "caleb"})
        self.assertEqual(flatten(response.context["groups"]), [self.qb])

    def test_rookie_board_orders_by_value(self) -> None:
        # Dynasty value is the ordering signal within a position now, replacing
        # the coarse search_rank proxy: the higher-value rookie surfaces first,
        # even though it sorts later alphabetically.
        top = make_player("100", "Zack Notable", position="TE")
        low = make_player("101", "Aaron Obscure", position="TE")
        make_value(top, now=80, prospect=80, horizon=80)
        make_value(low, now=20, prospect=20, horizon=20)
        response = self.client.get(self.url())
        te = next(g for g in response.context["groups"] if g["key"] == "TE")
        self.assertEqual(te["players"], [top, low])

    def test_rookie_rows_show_value(self) -> None:
        make_value(self.qb, now=70, prospect=90, horizon=85, tier=1)
        html = self.client.get(self.url()).content.decode()
        self.assertIn("T1", html)  # tier badge
        self.assertIn("Prospect 90", html)  # sub-score breakdown in the title
        self.assertIn("—", html)  # an unscored rookie still renders a dash

    def test_unknown_league_is_404(self) -> None:
        response = self.client.get(reverse("scouting:rookie_board", args=["nope"]))
        self.assertEqual(response.status_code, 404)

    def test_table_endpoint_returns_fragment_not_base(self) -> None:
        response = self.client.get(
            reverse("scouting:rookie_board_table", args=[self.league.slug])
        )
        names = [t.name for t in response.templates]
        self.assertIn("scouting/_rookie_table.html", names)
        self.assertNotIn("base.html", names)

    def test_query_budget(self) -> None:
        # +1 vs the pre-value board: the value overlay resolves the latest valued
        # season once; the per-row values themselves are correlated subqueries.
        with self.assertNumQueries(3):
            self.client.get(
                reverse("scouting:rookie_board_table", args=[self.league.slug])
            )

    def test_target_overlay_is_per_league(self) -> None:
        other = League.objects.create(
            name="Other", slug="other", normalized_name="other"
        )
        Target.objects.create(player=self.qb, league=other, stance="acquire")
        # The stance belongs to `other`, so this league's overlay must be empty.
        response = self.client.get(self.url())
        players = {p.pk: p for p in flatten(response.context["groups"])}
        # target_stance is a per-request annotation, invisible to django-stubs.
        self.assertIsNone(players[self.qb.pk].target_stance)  # type: ignore[attr-defined]


class SetTargetTests(ScoutingFixture):
    def url(self, player: Player) -> str:
        return reverse("scouting:set_target", args=[self.league.slug, player.pk])

    def test_creates_target_scoped_to_league(self) -> None:
        response = self.client.post(
            self.url(self.qb), {"stance": "acquire", "tier": "1", "priority": "high"}
        )
        self.assertEqual(response.status_code, 200)
        target = Target.objects.get(player=self.qb, league=self.league)
        self.assertEqual(target.stance, "acquire")
        self.assertEqual(target.tier, 1)
        self.assertEqual(target.priority, "high")
        self.assertContains(response, "Acquire")
        # The editor stays open after an edit so tier/priority can follow.
        self.assertContains(response, " open>")

    def test_updates_existing_target_in_place(self) -> None:
        Target.objects.create(
            player=self.qb, league=self.league, stance="acquire", tier=1
        )
        self.client.post(self.url(self.qb), {"stance": "avoid", "tier": "3"})
        self.assertEqual(
            Target.objects.filter(player=self.qb, league=self.league).count(), 1
        )
        self.assertEqual(Target.objects.get(player=self.qb).stance, "avoid")

    def test_blank_stance_clears(self) -> None:
        Target.objects.create(player=self.qb, league=self.league, stance="acquire")
        self.client.post(self.url(self.qb), {"stance": ""})
        self.assertFalse(Target.objects.filter(player=self.qb).exists())

    def test_unknown_stance_clears(self) -> None:
        Target.objects.create(player=self.qb, league=self.league, stance="acquire")
        self.client.post(self.url(self.qb), {"stance": "maybe"})
        self.assertFalse(Target.objects.filter(player=self.qb).exists())

    def test_bad_tier_stored_null(self) -> None:
        self.client.post(self.url(self.qb), {"stance": "acquire", "tier": "x"})
        self.assertIsNone(Target.objects.get(player=self.qb).tier)

    def test_missing_priority_defaults_medium(self) -> None:
        self.client.post(self.url(self.qb), {"stance": "acquire"})
        self.assertEqual(Target.objects.get(player=self.qb).priority, "medium")

    def test_get_is_rejected(self) -> None:
        self.assertEqual(self.client.get(self.url(self.qb)).status_code, 405)


class AddNoteTests(ScoutingFixture):
    def url(self, player: Player) -> str:
        return reverse("scouting:add_note", args=[self.league.slug, player.pk])

    def test_creates_note_and_shows_count(self) -> None:
        response = self.client.post(self.url(self.qb), {"body": "Elite arm talent"})
        self.assertEqual(response.status_code, 200)
        note = ScoutingNote.objects.get(player=self.qb)
        self.assertEqual(note.league, self.league)
        self.assertContains(response, "📝 1")

    def test_blank_note_ignored(self) -> None:
        self.client.post(self.url(self.qb), {"body": "  "})
        self.assertFalse(ScoutingNote.objects.exists())

    def test_get_is_rejected(self) -> None:
        self.assertEqual(self.client.get(self.url(self.qb)).status_code, 405)


class TargetWidgetTests(ScoutingFixture):
    def url(self, player: Player) -> str:
        return reverse("scouting:target_widget", args=[self.league.slug, player.pk])

    def test_renders_control_for_untargeted_player(self) -> None:
        response = self.client.get(self.url(self.qb))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "— none —")
        self.assertNotContains(response, "Acquire ·")
        # Lazy-loaded on a roster screen, it starts collapsed.
        self.assertNotContains(response, " open>")

    def test_reflects_existing_target(self) -> None:
        Target.objects.create(
            player=self.qb, league=self.league, stance="acquire", tier=2
        )
        response = self.client.get(self.url(self.qb))
        self.assertContains(response, "Acquire · T2")


class TargetBoardTests(ScoutingFixture):
    def setUp(self) -> None:
        Target.objects.create(
            player=self.rival_star, league=self.league, stance="acquire", tier=1
        )
        Target.objects.create(
            player=self.my_star, league=self.league, stance="acquire", tier=2
        )
        Target.objects.create(player=self.qb, league=self.league, stance="avoid")

    def url(self) -> str:
        return reverse("scouting:target_board", args=[self.league.slug])

    def test_lists_targets_grouped_by_stance(self) -> None:
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        keys = [g["key"] for g in response.context["groups"]]
        self.assertEqual(keys, ["acquire", "avoid"])
        self.assertEqual(response.context["count"], 3)

    def test_roster_location_labels(self) -> None:
        response = self.client.get(self.url())
        self.assertContains(response, "Rivals")
        self.assertContains(response, "My Squad")
        self.assertContains(response, "mine")  # my_star is on my team

    def test_untargeted_free_agent_label(self) -> None:
        # qb is a rookie, not rostered anywhere → "free agent".
        self.assertContains(self.client.get(self.url()), "free agent")

    def test_stance_filter(self) -> None:
        response = self.client.get(self.url(), {"stance": "avoid"})
        players = flatten(response.context["groups"])
        self.assertEqual(players, [self.qb])

    def test_invalid_stance_ignored(self) -> None:
        response = self.client.get(self.url(), {"stance": "bogus"})
        self.assertEqual(response.context["count"], 3)

    def test_table_endpoint_returns_fragment_not_base(self) -> None:
        response = self.client.get(
            reverse("scouting:target_board_table", args=[self.league.slug])
        )
        names = [t.name for t in response.templates]
        self.assertIn("scouting/_targets_table.html", names)
        self.assertNotIn("base.html", names)

    def test_target_rows_show_value(self) -> None:
        make_value(self.rival_star, now=88, prospect=40, horizon=60, tier=2)
        html = self.client.get(self.url()).content.decode()
        self.assertIn("T2", html)  # tier badge on the valued target
        self.assertIn("Now 88", html)  # sub-score breakdown in the title
        self.assertIn("—", html)  # an unscored target still renders a dash
