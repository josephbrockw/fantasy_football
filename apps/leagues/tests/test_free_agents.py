from urllib.parse import urlencode

from django.test import TestCase
from django.urls import reverse

from apps.leagues.models import League, LeagueSeason, Manager, RosterSlot, Team
from apps.leagues.views import FREE_AGENT_DEFAULT_SORT, FREE_AGENT_SORTS
from apps.players.models import Player, PlayerValue, TrendingPlayer
from apps.players.valuation import DEFAULT_PROFILE

# Starts a kicker nowhere — so kickers must not appear on the board.
ROSTER_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "DEF", "BN", "BN"]


def make_player(sleeper_id: str, name: str, **fields) -> Player:
    defaults = {"position": "WR", "team": "CIN", "age": 25, "years_exp": 3}
    defaults.update(fields)
    return Player.objects.create(sleeper_id=sleeper_id, full_name=name, **defaults)


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


class FreeAgentFixture(TestCase):
    league: League
    season: LeagueSeason
    past_season: LeagueSeason
    team: Team
    rostered: Player
    past_only: Player
    hot: Player
    cold: Player
    old_wr: Player
    young_rb: Player
    kicker: Player
    inactive: Player

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="The Dynasty", normalized_name="thedynasty", slug="the-dynasty"
        )
        cls.season = LeagueSeason.objects.create(
            league=cls.league,
            season="2026",
            sleeper_league_id="l2026",
            roster_positions=ROSTER_POSITIONS,
        )
        cls.past_season = LeagueSeason.objects.create(
            league=cls.league,
            season="2025",
            sleeper_league_id="l2025",
            roster_positions=ROSTER_POSITIONS,
        )
        manager = Manager.objects.create(sleeper_user_id="u1", display_name="Me")
        cls.team = Team.objects.create(
            league_season=cls.season, roster_id=1, manager=manager
        )
        past_team = Team.objects.create(
            league_season=cls.past_season, roster_id=1, manager=manager
        )

        cls.rostered = make_player("1", "Rostered Guy")
        cls.past_only = make_player("2", "Past Season Only")
        cls.hot = make_player("3", "Hot Pickup", position="RB", age=23)
        cls.cold = make_player("4", "Nobody Wants Him", position="TE", age=29)
        cls.old_wr = make_player("5", "Veteran Receiver", age=33)
        cls.young_rb = make_player("6", "Young Back", position="RB", age=22)
        cls.kicker = make_player("7", "Some Kicker", position="K", age=28)
        cls.inactive = make_player(
            "8", "Benched Forever", position="QB", status="Inactive"
        )

        RosterSlot.objects.create(team=cls.team, player=cls.rostered)
        RosterSlot.objects.create(team=past_team, player=cls.past_only)

        TrendingPlayer.objects.create(
            player=cls.hot, kind=TrendingPlayer.Kind.ADD, count=5000
        )
        TrendingPlayer.objects.create(
            player=cls.young_rb, kind=TrendingPlayer.Kind.ADD, count=100
        )

        # Dynasty values drive the default order and the profile re-blend. hot is
        # the top overall; old_wr is a win-now vet (high now, low prospect) and
        # young_rb a prospect (the reverse), so a contend↔rebuild switch flips
        # them. past_only is intentionally left unscored → renders "—".
        make_value(cls.hot, now=90, prospect=80, horizon=80, tier=1)
        make_value(cls.young_rb, now=50, prospect=90, horizon=90, tier=2)
        make_value(cls.old_wr, now=85, prospect=20, horizon=30, tier=3)
        make_value(cls.cold, now=30, prospect=30, horizon=40, tier=5)


class FreeAgentQuerysetTests(FreeAgentFixture):
    def url(self, **params) -> str:
        base = reverse("leagues:free_agents", args=[self.league.slug])
        return f"{base}?{urlencode(params)}" if params else base

    def players(self, **params) -> list[Player]:
        return list(self.client.get(self.url(**params)).context["players"])

    def test_excludes_players_rostered_this_season(self) -> None:
        self.assertNotIn(self.rostered, self.players())

    def test_includes_players_rostered_only_in_a_past_season(self) -> None:
        """Being on someone's 2025 roster doesn't make you unavailable in 2026."""
        self.assertIn(self.past_only, self.players())

    def test_excludes_positions_the_league_does_not_roster(self) -> None:
        """This league starts no kicker, so kickers aren't free agents here."""
        self.assertNotIn(self.kicker, self.players())

    def test_excludes_inactive_by_default(self) -> None:
        self.assertNotIn(self.inactive, self.players())

    def test_inactive_can_be_included(self) -> None:
        self.assertIn(self.inactive, self.players(inactive="1"))

    def test_position_filter(self) -> None:
        found = self.players(pos="RB")
        self.assertEqual(set(found), {self.hot, self.young_rb})

    def test_max_age_filter(self) -> None:
        found = self.players(max_age=25)
        self.assertIn(self.young_rb, found)
        self.assertNotIn(self.old_wr, found)

    def test_search_by_name_is_case_insensitive(self) -> None:
        self.assertEqual(self.players(q="hot pickup"), [self.hot])

    def test_search_matches_partial_names(self) -> None:
        self.assertIn(self.hot, self.players(q="pick"))

    def test_filters_combine(self) -> None:
        self.assertEqual(self.players(pos="RB", max_age=22), [self.young_rb])


class FreeAgentOrderingTests(FreeAgentFixture):
    def url(self, **params) -> str:
        base = reverse("leagues:free_agents", args=[self.league.slug])
        return f"{base}?{urlencode(params)}" if params else base

    def players(self, **params) -> list[Player]:
        return list(self.client.get(self.url(**params)).context["players"])

    def test_default_sort_orders_by_dynasty_value(self) -> None:
        # Default profile is "contend" (.70 now), so the win-now vet old_wr edges
        # the prospect young_rb into second behind the top-overall hot.
        found = self.players()
        self.assertEqual(found[0], self.hot)
        self.assertEqual(found[1], self.old_wr)

    def test_value_sort_ascending_flips_and_nulls_last(self) -> None:
        found = self.players(sort="value", dir="asc")
        self.assertEqual(found[0], self.cold)  # lowest value first
        self.assertEqual(found[-1], self.past_only)  # unscored sorts last

    def test_players_without_trending_data_sort_last_not_missing(self) -> None:
        found = self.players(sort="trending")
        self.assertIn(self.cold, found)
        self.assertGreater(found.index(self.cold), found.index(self.hot))

    def test_dynasty_value_breaks_ties_not_search_rank(self) -> None:
        # old_wr and cold both have no trending adds (a tie on the primary sort);
        # the higher dynasty value now decides the order, replacing search_rank.
        found = self.players(sort="trending")
        self.assertLess(found.index(self.old_wr), found.index(self.cold))

    def test_value_column_renders_with_breakdown(self) -> None:
        html = self.client.get(self.url()).content.decode()
        self.assertIn("T1", html)  # hot's tier badge
        self.assertIn("Now 90", html)  # hot's sub-score breakdown in the title
        self.assertIn("—", html)  # past_only is unscored → dash

    def test_profile_reblends_ordering_without_recompute(self) -> None:
        # Same stored sub-scores, re-weighted at read time: a win-now vet leads
        # under "contend", the prospect leads under "rebuild".
        contend = self.players(profile="contend")
        self.assertLess(contend.index(self.old_wr), contend.index(self.young_rb))
        rebuild = self.players(profile="rebuild")
        self.assertLess(rebuild.index(self.young_rb), rebuild.index(self.old_wr))

    def test_unknown_profile_falls_back_to_default(self) -> None:
        response = self.client.get(self.url(profile="nonsense"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["profile"], DEFAULT_PROFILE)

    def test_query_budget_is_bounded_by_the_overlay(self) -> None:
        # The value overlay resolves the latest valued season once; per-row
        # values are correlated subqueries, not an N+1.
        url = reverse("leagues:free_agents_table", args=[self.league.slug])
        with self.assertNumQueries(5):
            self.client.get(url)

    def test_sort_by_age_ascending(self) -> None:
        found = self.players(sort="age", dir="asc")
        self.assertEqual(found[0], self.young_rb)

    def test_sort_by_age_descending(self) -> None:
        found = self.players(sort="age", dir="desc")
        self.assertEqual(found[0], self.old_wr)

    def test_invalid_sort_falls_back_to_default(self) -> None:
        response = self.client.get(self.url(sort="'; DROP TABLE players; --"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], FREE_AGENT_DEFAULT_SORT)

    def test_invalid_max_age_is_ignored(self) -> None:
        response = self.client.get(self.url(max_age="twenty"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["max_age"])

    def test_every_sort_key_works(self) -> None:
        for key in FREE_AGENT_SORTS:
            with self.subTest(key=key):
                self.assertEqual(self.client.get(self.url(sort=key)).status_code, 200)


class FreeAgentViewTests(FreeAgentFixture):
    def url(self, **params) -> str:
        base = reverse("leagues:free_agents", args=[self.league.slug])
        return f"{base}?{urlencode(params)}" if params else base

    def test_renders(self) -> None:
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Free agents")
        self.assertContains(response, "Hot Pickup")

    def test_shows_trending_counts(self) -> None:
        self.assertContains(self.client.get(self.url()), "+5000")

    def test_position_chips_come_from_the_league(self) -> None:
        """Deduplicated, and no kicker chip for a league that starts none."""
        positions = self.client.get(self.url()).context["positions"]

        self.assertEqual(positions, ["QB", "RB", "WR", "TE", "DEF"])
        self.assertNotIn("K", positions)

    def test_unknown_league_is_404(self) -> None:
        response = self.client.get(reverse("leagues:free_agents", args=["nope"]))
        self.assertEqual(response.status_code, 404)

    def test_empty_result_renders_a_message(self) -> None:
        self.assertContains(self.client.get(self.url(q="zzzznobody")), "No free agents")

    def test_querystring_preserves_filters_for_sort_links(self) -> None:
        context = self.client.get(self.url(pos="RB", max_age=25, q="a")).context
        self.assertIn("pos=RB", context["querystring"])
        self.assertIn("max_age=25", context["querystring"])
        self.assertIn("q=a", context["querystring"])

    def test_querystring_includes_the_inactive_toggle(self) -> None:
        context = self.client.get(self.url(inactive="1")).context
        self.assertIn("inactive=1", context["querystring"])


class FreeAgentPaginationTests(FreeAgentFixture):
    def test_paginates(self) -> None:
        for index in range(60):
            make_player(f"bulk{index}", f"Bulk Player {index:02d}", position="WR")

        url = reverse("leagues:free_agents", args=[self.league.slug])
        first = self.client.get(url)
        second = self.client.get(url, {"page": 2})

        self.assertEqual(len(first.context["players"]), 50)
        self.assertTrue(first.context["page_obj"].has_next())
        self.assertEqual(second.context["page_obj"].number, 2)


class FreeAgentFragmentTests(FreeAgentFixture):
    def url(self, **params) -> str:
        base = reverse("leagues:free_agents_table", args=[self.league.slug])
        return f"{base}?{urlencode(params)}" if params else base

    def test_returns_a_bare_fragment(self) -> None:
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        used = {t.name for t in response.templates if t.name}
        self.assertIn("leagues/_free_agent_table.html", used)
        self.assertNotIn("base.html", used)
        self.assertNotContains(response, "<html")

    def test_fragment_respects_filters(self) -> None:
        response = self.client.get(self.url(pos="RB"))

        self.assertContains(response, "Hot Pickup")
        self.assertNotContains(response, "Veteran Receiver")


class NoSeasonTests(TestCase):
    def test_league_without_seasons_renders_an_empty_state(self) -> None:
        league = League.objects.create(
            name="Fresh", normalized_name="fresh", slug="fresh"
        )
        response = self.client.get(reverse("leagues:free_agents", args=[league.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["season"])
        self.assertEqual(list(response.context["players"]), [])
        self.assertContains(response, "No seasons synced")


class LeagueWithoutRosterPositionsTests(TestCase):
    def test_all_positions_allowed_when_league_declares_none(self) -> None:
        league = League.objects.create(name="L", normalized_name="l", slug="l")
        LeagueSeason.objects.create(
            league=league, season="2026", sleeper_league_id="x", roster_positions=[]
        )
        kicker = make_player("k1", "A Kicker", position="K")

        response = self.client.get(reverse("leagues:free_agents", args=[league.slug]))

        self.assertIn(kicker, list(response.context["players"]))
