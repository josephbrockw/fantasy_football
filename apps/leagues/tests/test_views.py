from urllib.parse import urlencode

from django.test import TestCase
from django.urls import reverse

from apps.leagues.models import League, LeagueSeason, Manager, RosterSlot, Team
from apps.leagues.views import DEFAULT_SORT, SORT_FIELDS
from apps.players.models import Player, PlayerValue
from apps.sleeper.models import SyncRun

# Mirrors a real superflex dynasty league: six starting slots plus reserves.
ROSTER_POSITIONS = [
    "QB",
    "RB",
    "WR",
    "FLEX",
    "SUPER_FLEX",
    "DEF",
    "BN",
    "BN",
    "TAXI",
]


def make_player(sleeper_id: str, name: str, **fields) -> Player:
    defaults = {"position": "WR", "team": "CIN", "age": 25, "years_exp": 3}
    defaults.update(fields)
    return Player.objects.create(sleeper_id=sleeper_id, full_name=name, **defaults)


class LeagueFixture(TestCase):
    """A superflex league with a partly-filled starting lineup."""

    # Declared so mypy can see what setUpTestData assigns.
    league: League
    season: LeagueSeason
    me: Manager
    rival: Manager
    my_team: Team
    rival_team: Team
    starting_qb: Player
    superflex_qb: Player
    starting_rb: Player
    starting_wr: Player
    bench_te: Player
    bench_qb: Player
    taxi_rb: Player

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="The Dynasty", normalized_name="thedynasty", slug="the-dynasty"
        )
        cls.season = LeagueSeason.objects.create(
            league=cls.league,
            season="2026",
            sleeper_league_id="l2026",
            total_rosters=2,
            status="in_season",
            roster_positions=ROSTER_POSITIONS,
        )
        cls.me = Manager.objects.create(
            sleeper_user_id="u_me", display_name="Me", is_me=True
        )
        cls.rival = Manager.objects.create(
            sleeper_user_id="u_rival", display_name="Rival"
        )
        cls.my_team = Team.objects.create(
            league_season=cls.season,
            roster_id=1,
            manager=cls.me,
            team_name="My Squad",
            wins=9,
            losses=4,
            points_for="1500.25",
        )
        cls.rival_team = Team.objects.create(
            league_season=cls.season,
            roster_id=2,
            manager=cls.rival,
            team_name="Their Squad",
            wins=4,
            losses=9,
        )

        cls.starting_qb = make_player("1", "Patrick Mahomes", position="QB", age=31)
        cls.superflex_qb = make_player("2", "Justin Herbert", position="QB", age=28)
        cls.starting_rb = make_player(
            "3", "Bijan Robinson", position="RB", team="ATL", age=24
        )
        cls.starting_wr = make_player("4", "Ja'Marr Chase", age=26, years_exp=5)
        cls.bench_te = make_player(
            "5", "Injured Guy", position="TE", age=30, injury_status="Questionable"
        )
        cls.bench_qb = make_player("6", "Ageless One", position="QB", age=None)
        cls.taxi_rb = make_player(
            "7",
            "Jeremiyah Love",
            position="RB",
            team="ARI",
            age=21,
            years_exp=0,
            rookie_year=2026,
        )

        # Lineup slots 3 (FLEX) and 5 (DEF) are deliberately left empty.
        for player, order, position in (
            (cls.starting_qb, 0, "QB"),
            (cls.starting_rb, 1, "RB"),
            (cls.starting_wr, 2, "WR"),
            (cls.superflex_qb, 4, "SUPER_FLEX"),
        ):
            RosterSlot.objects.create(
                team=cls.my_team,
                player=player,
                slot=RosterSlot.Slot.STARTER,
                lineup_order=order,
                lineup_position=position,
            )
        RosterSlot.objects.create(
            team=cls.my_team, player=cls.bench_te, slot=RosterSlot.Slot.BENCH
        )
        RosterSlot.objects.create(
            team=cls.my_team, player=cls.bench_qb, slot=RosterSlot.Slot.BENCH
        )
        RosterSlot.objects.create(
            team=cls.my_team, player=cls.taxi_rb, slot=RosterSlot.Slot.TAXI
        )


class DashboardTests(LeagueFixture):
    def test_renders_my_team_and_leagues(self) -> None:
        response = self.client.get(reverse("leagues:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Squad")
        self.assertContains(response, "The Dynasty")
        self.assertContains(response, "9-4")

    def test_one_card_per_league_not_per_season(self) -> None:
        """A dynasty has many seasons but should show a single current card."""
        for season in ("2025", "2024"):
            past = LeagueSeason.objects.create(
                league=self.league, season=season, sleeper_league_id=f"l{season}"
            )
            Team.objects.create(
                league_season=past, roster_id=1, manager=self.me, team_name="My Squad"
            )

        response = self.client.get(reverse("leagues:dashboard"))

        my_teams = response.context["my_teams"]
        self.assertEqual(len(my_teams), 1)
        self.assertEqual(my_teams[0].league_season.season, "2026")

    def test_one_card_per_league_across_multiple_leagues(self) -> None:
        other = League.objects.create(
            name="Second League", normalized_name="secondleague", slug="second-league"
        )
        other_season = LeagueSeason.objects.create(
            league=other, season="2026", sleeper_league_id="other2026"
        )
        Team.objects.create(
            league_season=other_season, roster_id=1, manager=self.me, team_name="Other"
        )

        response = self.client.get(reverse("leagues:dashboard"))

        self.assertEqual(len(response.context["my_teams"]), 2)

    def test_shows_player_count(self) -> None:
        response = self.client.get(reverse("leagues:dashboard"))
        self.assertEqual(response.context["my_teams"][0].player_count, 7)

    def test_lists_sync_freshness(self) -> None:
        SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.SUCCESS,
            finished_at="2026-07-01T12:00:00Z",
            records_written=1045,
        )
        response = self.client.get(reverse("leagues:dashboard"))

        self.assertContains(response, "1045 rows")
        self.assertContains(response, "never")

    def test_excludes_rival_teams(self) -> None:
        response = self.client.get(reverse("leagues:dashboard"))
        self.assertEqual([str(t) for t in response.context["my_teams"]], ["My Squad"])


class EmptyDashboardTests(TestCase):
    def test_renders_empty_state_with_no_data(self) -> None:
        response = self.client.get(reverse("leagues:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_data"])
        self.assertContains(response, "No data yet")
        self.assertContains(response, "make sync-players")


class LeagueOverviewTests(LeagueFixture):
    def url(self) -> str:
        return reverse("leagues:league_overview", args=[self.league.slug])

    def test_lists_standings(self) -> None:
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Squad")
        self.assertContains(response, "Their Squad")

    def test_orders_by_record(self) -> None:
        """Standings sort by wins, then points.

        The annotate() makes this an aggregate query and Django drops
        Meta.ordering for those, so the ordering must be explicit. These teams
        are created after the fixture's, so insertion order deliberately
        disagrees with record order.
        """
        best = Team.objects.create(
            league_season=self.season, roster_id=3, team_name="Best", wins=12, losses=1
        )
        worst = Team.objects.create(
            league_season=self.season, roster_id=4, team_name="Worst", wins=1, losses=12
        )

        teams = list(self.client.get(self.url()).context["teams"])

        self.assertEqual(teams[0], best)
        self.assertEqual(teams[-1], worst)
        self.assertEqual(
            [t.wins for t in teams], sorted((t.wins for t in teams), reverse=True)
        )

    def test_orders_by_points_when_records_tie(self) -> None:
        Team.objects.create(
            league_season=self.season,
            roster_id=3,
            team_name="Same record, more points",
            wins=9,
            losses=4,
            points_for="9999.00",
        )
        response = self.client.get(self.url())
        self.assertEqual(str(response.context["teams"][0]), "Same record, more points")

    def test_defaults_to_current_season(self) -> None:
        LeagueSeason.objects.create(
            league=self.league, season="2025", sleeper_league_id="l2025"
        )
        self.assertEqual(self.client.get(self.url()).context["season"].season, "2026")

    def test_season_query_param_switches_season(self) -> None:
        older = LeagueSeason.objects.create(
            league=self.league, season="2025", sleeper_league_id="l2025"
        )
        response = self.client.get(self.url(), {"season": "2025"})

        self.assertEqual(response.context["season"], older)
        self.assertEqual(list(response.context["teams"]), [])

    def test_unknown_season_falls_back_to_none(self) -> None:
        response = self.client.get(self.url(), {"season": "1999"})

        self.assertIsNone(response.context["season"])
        self.assertContains(response, "No seasons synced")

    def test_unknown_league_is_404(self) -> None:
        response = self.client.get(reverse("leagues:league_overview", args=["nope"]))
        self.assertEqual(response.status_code, 404)

    def test_query_count_is_bounded(self) -> None:
        with self.assertNumQueries(3):
            self.client.get(self.url())


class StartingLineupTests(LeagueFixture):
    def url(self, **params) -> str:
        base = reverse("leagues:team_detail", args=[self.my_team.pk])
        return f"{base}?{urlencode(params)}" if params else base

    def test_lineup_follows_league_slot_order(self) -> None:
        """Not alphabetical, not insertion order — the league's declared order."""
        lineup = self.client.get(self.url()).context["lineup"]

        self.assertEqual(
            [row.position for row in lineup],
            ["QB", "RB", "WR", "FLEX", "SUPER_FLEX", "DEF"],
        )

    def test_each_slot_holds_the_right_player(self) -> None:
        lineup = self.client.get(self.url()).context["lineup"]
        filled = {row.position: row.slot.player for row in lineup if row.slot}

        self.assertEqual(filled["QB"], self.starting_qb)
        self.assertEqual(filled["RB"], self.starting_rb)
        self.assertEqual(filled["WR"], self.starting_wr)
        self.assertEqual(filled["SUPER_FLEX"], self.superflex_qb)

    def test_two_quarterbacks_are_legal_via_superflex(self) -> None:
        """The reported bug: a legal superflex lineup looked like stacked QBs.

        Both QBs start, but one fills QB and the other SUPER_FLEX — the page
        must make that distinction visible.
        """
        response = self.client.get(self.url())
        lineup = response.context["lineup"]

        starting_qbs = [
            row for row in lineup if row.slot and row.slot.player.position == "QB"
        ]
        self.assertEqual(len(starting_qbs), 2)
        self.assertEqual(
            sorted(row.position for row in starting_qbs), ["QB", "SUPER_FLEX"]
        )
        self.assertContains(response, "SUPER_FLEX")

    def test_empty_slots_are_surfaced(self) -> None:
        response = self.client.get(self.url())
        empties = [row.position for row in response.context["lineup"] if row.is_empty]

        self.assertEqual(empties, ["FLEX", "DEF"])
        self.assertContains(response, "empty")

    def test_flex_slots_are_flagged(self) -> None:
        lineup = self.client.get(self.url()).context["lineup"]
        flex = [row.position for row in lineup if row.is_flex]
        self.assertEqual(flex, ["FLEX", "SUPER_FLEX"])

    def test_starters_show_value(self) -> None:
        PlayerValue.objects.create(
            player=self.starting_qb,
            season=2026,
            position="QB",
            value=91.0,
            tier=1,
        )
        response = self.client.get(self.url())
        # The tier badge only renders inside the value cell, so its presence
        # proves the starting-lineup value overlay reached the row.
        self.assertContains(response, "T1")

    def test_lineup_is_unaffected_by_sort_params(self) -> None:
        """Sorting applies to reserves; the lineup order is semantic."""
        lineup = self.client.get(self.url(sort="age", dir="desc")).context["lineup"]
        self.assertEqual(
            [row.position for row in lineup],
            ["QB", "RB", "WR", "FLEX", "SUPER_FLEX", "DEF"],
        )

    def test_starter_without_alignment_still_appears(self) -> None:
        """Data synced before lineup tracking must not silently vanish."""
        orphan = make_player("99", "Legacy Starter", position="WR")
        RosterSlot.objects.create(
            team=self.my_team, player=orphan, slot=RosterSlot.Slot.STARTER
        )

        lineup = self.client.get(self.url()).context["lineup"]

        self.assertIn(orphan, [row.slot.player for row in lineup if row.slot])

    def test_league_with_no_roster_positions(self) -> None:
        self.season.roster_positions = []
        self.season.save(update_fields=["roster_positions"])

        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        # The four aligned starters fall through to the orphan path.
        self.assertEqual(len(response.context["lineup"]), 4)


class ReservesTests(LeagueFixture):
    def url(self, **params) -> str:
        base = reverse("leagues:team_detail", args=[self.my_team.pk])
        return f"{base}?{urlencode(params)}" if params else base

    def test_groups_bench_taxi_and_ir(self) -> None:
        response = self.client.get(self.url())
        groups = {value: rows for value, _, rows in response.context["reserve_groups"]}

        self.assertEqual(len(groups["bench"]), 2)
        self.assertEqual([s.player for s in groups["taxi"]], [self.taxi_rb])
        self.assertNotIn("ir", groups)

    def test_starters_are_excluded_from_reserves(self) -> None:
        response = self.client.get(self.url())
        players = [
            slot.player
            for _, _, rows in response.context["reserve_groups"]
            for slot in rows
        ]
        self.assertNotIn(self.starting_qb, players)

    def test_default_sort_is_football_position_order(self) -> None:
        """QB before TE — not the alphabetical QB, TE order."""
        response = self.client.get(self.url())
        bench = next(
            rows
            for value, _, rows in response.context["reserve_groups"]
            if value == "bench"
        )
        self.assertEqual([s.player.position for s in bench], ["QB", "TE"])

    def test_position_filter(self) -> None:
        response = self.client.get(self.url(pos="RB"))
        players = [
            slot.player
            for _, _, rows in response.context["reserve_groups"]
            for slot in rows
        ]
        self.assertEqual(players, [self.taxi_rb])

    def test_position_chips_are_football_ordered_and_deduplicated(self) -> None:
        another_qb = make_player("100", "Backup Two", position="QB")
        RosterSlot.objects.create(
            team=self.my_team, player=another_qb, slot=RosterSlot.Slot.IR
        )

        positions = self.client.get(self.url()).context["positions"]

        self.assertEqual(positions, ["QB", "RB", "TE"])

    def test_chips_exclude_starter_only_positions(self) -> None:
        """Chips filter the reserves, so they list reserve positions only."""
        positions = self.client.get(self.url()).context["positions"]
        self.assertNotIn("WR", positions)

    def test_sort_by_age_descending(self) -> None:
        response = self.client.get(self.url(sort="age", dir="desc"))
        self.assertEqual(response.context["sort"], "age")
        self.assertEqual(response.context["dir"], "desc")

    def test_nulls_sort_last(self) -> None:
        response = self.client.get(self.url(sort="age", dir="asc"))
        bench = next(
            rows
            for value, _, rows in response.context["reserve_groups"]
            if value == "bench"
        )
        self.assertEqual(bench[0].player, self.bench_te)
        self.assertEqual(bench[-1].player, self.bench_qb)

    def test_invalid_sort_falls_back_to_default(self) -> None:
        response = self.client.get(self.url(sort="'; DROP TABLE players; --"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], DEFAULT_SORT)

    def test_invalid_direction_falls_back_to_asc(self) -> None:
        response = self.client.get(self.url(dir="sideways"))
        self.assertEqual(response.context["dir"], "asc")

    def test_every_sort_key_works(self) -> None:
        for key in SORT_FIELDS:
            with self.subTest(key=key):
                self.assertEqual(self.client.get(self.url(sort=key)).status_code, 200)

    def test_unknown_team_is_404(self) -> None:
        self.assertEqual(
            self.client.get(reverse("leagues:team_detail", args=[99999])).status_code,
            404,
        )

    def test_query_count_is_bounded(self) -> None:
        # +2 vs the pre-value page: the starting-lineup and reserve overlays each
        # resolve the latest valued season once; the per-row values are
        # correlated subqueries, not an N+1.
        with self.assertNumQueries(6):
            self.client.get(self.url())

    def test_reserves_show_value(self) -> None:
        PlayerValue.objects.create(
            player=self.bench_te,
            season=2026,
            position="TE",
            value=63.0,
            tier=4,
        )
        response = self.client.get(self.url())
        # The tier badge only renders inside the value cell, so its presence
        # proves the reserve value overlay reached the row.
        self.assertContains(response, "T4")


class ReserveFragmentTests(LeagueFixture):
    def url(self, **params) -> str:
        base = reverse("leagues:team_reserves", args=[self.my_team.pk])
        return f"{base}?{urlencode(params)}" if params else base

    def test_returns_a_bare_fragment(self) -> None:
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        used = {t.name for t in response.templates if t.name}
        self.assertIn("leagues/_reserve_tables.html", used)
        self.assertNotIn("base.html", used)
        self.assertNotContains(response, "<html")

    def test_fragment_respects_filters(self) -> None:
        response = self.client.get(self.url(pos="RB"))

        self.assertContains(response, "Jeremiyah Love")
        self.assertNotContains(response, "Injured Guy")

    def test_fragment_never_contains_starters(self) -> None:
        response = self.client.get(self.url())
        self.assertNotContains(response, "Patrick Mahomes")

    def test_empty_filter_result_renders_a_message(self) -> None:
        self.assertContains(self.client.get(self.url(pos="K")), "No reserve players")

    def test_unknown_team_is_404(self) -> None:
        self.assertEqual(
            self.client.get(reverse("leagues:team_reserves", args=[99999])).status_code,
            404,
        )
