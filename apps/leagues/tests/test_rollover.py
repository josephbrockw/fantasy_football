"""The season-rollover behaviour: one dynasty, a new league_id every year."""

from django.test import TestCase

from apps.leagues.models import League, LeagueSeason, Manager
from apps.leagues.services import (
    MAX_CHAIN_DEPTH,
    normalize_league_name,
    sync_leagues,
    walk_season_chain,
)
from apps.leagues.tests.factories import (
    ME,
    RIVAL,
    FakeLeagueClient,
    make_league,
    make_roster,
    make_user,
)

L2026, L2027 = "league_2026", "league_2027"


def two_season_client(previous_league_id: str | None, **kwargs) -> FakeLeagueClient:
    """A dynasty in 2027 whose 2026 season may or may not be chained to it."""
    older = make_league(L2026, "2026")
    newer = make_league(L2027, "2027", previous_league_id=previous_league_id)
    users = [make_user(ME, team_name="My Squad"), make_user(RIVAL, "rival", "Rival")]
    return FakeLeagueClient(
        user_leagues=[newer],
        leagues={L2026: older, L2027: newer},
        rosters={
            L2026: [make_roster(1, ME), make_roster(2, RIVAL)],
            L2027: [make_roster(1, ME), make_roster(2, RIVAL)],
        },
        users={L2026: users, L2027: users},
        season="2027",
        **kwargs,
    )


class NormalizeLeagueNameTests(TestCase):
    def test_collapses_case_spacing_and_punctuation(self) -> None:
        for variant in ("The League", "the  league!", "TheLeague", "the-league"):
            with self.subTest(variant=variant):
                self.assertEqual(normalize_league_name(variant), "theleague")

    def test_distinct_names_stay_distinct(self) -> None:
        self.assertNotEqual(
            normalize_league_name("Dynasty A"), normalize_league_name("Dynasty B")
        )

    def test_handles_empty(self) -> None:
        self.assertEqual(normalize_league_name(""), "")


class ChainWalkTests(TestCase):
    def test_follows_previous_league_id_backwards(self) -> None:
        client = two_season_client(previous_league_id=L2026)
        chain = walk_season_chain(client, client.leagues[L2027])

        self.assertEqual([entry["league_id"] for entry in chain], [L2027, L2026])

    def test_stops_when_chain_ends(self) -> None:
        client = two_season_client(previous_league_id=None)
        chain = walk_season_chain(client, client.leagues[L2027])

        self.assertEqual([entry["league_id"] for entry in chain], [L2027])

    def test_self_reference_does_not_loop(self) -> None:
        loop = make_league("loop", "2026", previous_league_id="loop")
        client = FakeLeagueClient(leagues={"loop": loop})

        chain = walk_season_chain(client, loop)

        self.assertEqual(len(chain), 1)

    def test_cycle_does_not_loop(self) -> None:
        a = make_league("a", "2027", previous_league_id="b")
        b = make_league("b", "2026", previous_league_id="a")
        client = FakeLeagueClient(leagues={"a": a, "b": b})

        chain = walk_season_chain(client, a)

        self.assertEqual([entry["league_id"] for entry in chain], ["a", "b"])

    def test_depth_is_capped(self) -> None:
        leagues = {
            f"l{i}": make_league(f"l{i}", str(2030 - i), previous_league_id=f"l{i + 1}")
            for i in range(60)
        }
        client = FakeLeagueClient(leagues=leagues)

        chain = walk_season_chain(client, leagues["l0"])

        self.assertEqual(len(chain), MAX_CHAIN_DEPTH)

    def test_missing_ancestor_ends_the_walk(self) -> None:
        orphan = make_league("orphan", "2027", previous_league_id="gone")
        client = FakeLeagueClient(leagues={"orphan": orphan})

        chain = walk_season_chain(client, orphan)

        self.assertEqual(len(chain), 1)


class RolloverTests(TestCase):
    def test_chained_seasons_bind_to_one_league(self) -> None:
        sync_leagues(client=two_season_client(L2026), username="dynastyguy")

        self.assertEqual(League.objects.count(), 1)
        self.assertEqual(LeagueSeason.objects.count(), 2)
        self.assertEqual(
            sorted(LeagueSeason.objects.values_list("season", flat=True)),
            ["2026", "2027"],
        )

    def test_broken_chain_falls_back_to_name_match(self) -> None:
        """The requested behaviour: same league name means the same league."""
        # 2026 synced on its own, with no forward link.
        first = FakeLeagueClient(
            user_leagues=[make_league(L2026, "2026")],
            leagues={L2026: make_league(L2026, "2026")},
            rosters={L2026: [make_roster(1, ME)]},
            users={L2026: [make_user(ME)]},
            season="2026",
        )
        sync_leagues(client=first, username="dynastyguy")

        # 2027 arrives with previous_league_id missing, but the same name.
        sync_leagues(client=two_season_client(None), username="dynastyguy")

        self.assertEqual(League.objects.count(), 1)
        self.assertEqual(LeagueSeason.objects.count(), 2)

    def test_different_names_stay_separate(self) -> None:
        client = FakeLeagueClient(
            user_leagues=[
                make_league("a", "2026", name="Dynasty A"),
                make_league("b", "2026", name="Dynasty B"),
            ],
            leagues={
                "a": make_league("a", "2026", name="Dynasty A"),
                "b": make_league("b", "2026", name="Dynasty B"),
            },
            rosters={"a": [make_roster(1, ME)], "b": [make_roster(1, ME)]},
            users={"a": [make_user(ME)], "b": [make_user(ME)]},
            season="2026",
        )

        sync_leagues(client=client, username="dynastyguy")

        self.assertEqual(League.objects.count(), 2)
        self.assertEqual(
            sorted(League.objects.values_list("name", flat=True)),
            ["Dynasty A", "Dynasty B"],
        )

    def test_managers_persist_across_seasons(self) -> None:
        sync_leagues(client=two_season_client(L2026), username="dynastyguy")

        # Two managers total, not two per season.
        self.assertEqual(Manager.objects.count(), 2)
        rival = Manager.objects.get(sleeper_user_id=RIVAL)
        self.assertEqual(rival.teams.count(), 2)
        self.assertEqual(
            sorted(t.league_season.season for t in rival.teams.all()),
            ["2026", "2027"],
        )

    def test_resync_is_idempotent(self) -> None:
        sync_leagues(client=two_season_client(L2026), username="dynastyguy")
        sync_leagues(client=two_season_client(L2026), username="dynastyguy")

        self.assertEqual(League.objects.count(), 1)
        self.assertEqual(LeagueSeason.objects.count(), 2)
        self.assertEqual(Manager.objects.count(), 2)

    def test_slug_is_unique_across_distinct_leagues(self) -> None:
        """Two leagues can share a display name without colliding on slug."""
        League.objects.create(
            name="The Dynasty", normalized_name="somethingelse", slug="the-dynasty"
        )

        sync_leagues(client=two_season_client(L2026), username="dynastyguy")

        self.assertEqual(League.objects.count(), 2)
        self.assertEqual(len(set(League.objects.values_list("slug", flat=True))), 2)
