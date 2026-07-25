from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.players.models import Player
from apps.scouting.models import ScoutingNote, Target


def make_rookie(sleeper_id: str, name: str, **fields: object) -> Player:
    defaults: dict[str, object] = {
        "position": "WR",
        "team": "CIN",
        "age": 22,
        "years_exp": 0,
        "rookie_year": 2024,
    }
    defaults.update(fields)
    return Player.objects.create(sleeper_id=sleeper_id, full_name=name, **defaults)


class RookieBoardFixture(TestCase):
    qb: Player
    wr: Player
    rb: Player
    veteran: Player

    @classmethod
    def setUpTestData(cls) -> None:
        cls.qb = make_rookie("1", "Caleb Williams", position="QB", age=22)
        cls.wr = make_rookie("2", "Marvin Harrison", position="WR", age=21)
        cls.rb = make_rookie("3", "Blake Corum", position="RB", age=23)
        cls.veteran = make_rookie("9", "Patrick Mahomes", position="QB", years_exp=7)


class RookieBoardTests(RookieBoardFixture):
    def test_lists_only_rookies(self) -> None:
        response = self.client.get(reverse("scouting:rookie_board"))
        self.assertEqual(response.status_code, 200)
        players = list(response.context["players"])
        self.assertIn(self.qb, players)
        self.assertNotIn(self.veteran, players)
        self.assertContains(response, "Caleb Williams")
        self.assertNotContains(response, "Patrick Mahomes")

    def test_position_filter(self) -> None:
        response = self.client.get(reverse("scouting:rookie_board"), {"pos": "WR"})
        players = list(response.context["players"])
        self.assertEqual(players, [self.wr])

    def test_search_filter_is_case_insensitive(self) -> None:
        response = self.client.get(reverse("scouting:rookie_board"), {"q": "caleb"})
        self.assertEqual(list(response.context["players"]), [self.qb])

    def test_sort_whitelist_orders_by_age(self) -> None:
        response = self.client.get(
            reverse("scouting:rookie_board"), {"sort": "age", "dir": "desc"}
        )
        ages = [p.age for p in response.context["players"]]
        self.assertEqual(ages, sorted(ages, reverse=True))

    def test_injection_sort_falls_back_to_default(self) -> None:
        response = self.client.get(
            reverse("scouting:rookie_board"),
            {"sort": "age); drop table players;--"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "position")

    def test_rookie_year_filter_narrows_the_class(self) -> None:
        older = make_rookie("50", "Prior Classer", position="TE", rookie_year=2023)
        response = self.client.get(
            reverse("scouting:rookie_board"), {"rookie_year": "2024"}
        )
        players = list(response.context["players"])
        self.assertIn(self.qb, players)
        self.assertNotIn(older, players)
        # querystring carries the active filter for sort/pagination links.
        self.assertIn("rookie_year=2024", response.context["querystring"])

    def test_invalid_rookie_year_is_ignored(self) -> None:
        response = self.client.get(
            reverse("scouting:rookie_board"), {"rookie_year": "not-a-year"}
        )
        self.assertIsNone(response.context["rookie_year"])
        self.assertIn(self.qb, list(response.context["players"]))

    def test_table_endpoint_returns_fragment_not_base(self) -> None:
        response = self.client.get(reverse("scouting:rookie_board_table"))
        names = [t.name for t in response.templates]
        self.assertIn("scouting/_rookie_table.html", names)
        self.assertNotIn("base.html", names)

    def test_query_budget(self) -> None:
        # count + page query only — the target overlay and note count must not N+1.
        with self.assertNumQueries(2):
            self.client.get(reverse("scouting:rookie_board_table"))


class SetTargetTests(RookieBoardFixture):
    def url(self, player: Player) -> str:
        return reverse("scouting:set_target", args=[player.pk])

    def test_creates_target(self) -> None:
        response = self.client.post(
            self.url(self.qb),
            {"stance": "acquire", "tier": "1", "priority": "high"},
        )
        self.assertEqual(response.status_code, 200)
        target = Target.objects.get(player=self.qb)
        self.assertEqual(target.stance, Target.Stance.ACQUIRE)
        self.assertEqual(target.tier, 1)
        self.assertEqual(target.priority, Target.Priority.HIGH)

    def test_updates_existing_target_in_place(self) -> None:
        Target.objects.create(player=self.qb, stance=Target.Stance.ACQUIRE, tier=1)
        self.client.post(self.url(self.qb), {"stance": "avoid", "tier": "3"})
        self.assertEqual(Target.objects.filter(player=self.qb).count(), 1)
        target = Target.objects.get(player=self.qb)
        self.assertEqual(target.stance, Target.Stance.AVOID)
        self.assertEqual(target.tier, 3)

    def test_blank_stance_clears_target(self) -> None:
        Target.objects.create(player=self.qb, stance=Target.Stance.ACQUIRE)
        self.client.post(self.url(self.qb), {"stance": ""})
        self.assertFalse(Target.objects.filter(player=self.qb).exists())

    def test_unknown_stance_clears_target(self) -> None:
        Target.objects.create(player=self.qb, stance=Target.Stance.ACQUIRE)
        self.client.post(self.url(self.qb), {"stance": "maybe"})
        self.assertFalse(Target.objects.filter(player=self.qb).exists())

    def test_bad_tier_is_stored_as_null(self) -> None:
        self.client.post(
            self.url(self.qb), {"stance": "acquire", "tier": "not-a-number"}
        )
        self.assertIsNone(Target.objects.get(player=self.qb).tier)

    def test_missing_priority_defaults_to_medium(self) -> None:
        self.client.post(self.url(self.qb), {"stance": "acquire"})
        self.assertEqual(
            Target.objects.get(player=self.qb).priority, Target.Priority.MEDIUM
        )

    def test_get_is_rejected(self) -> None:
        self.assertEqual(self.client.get(self.url(self.qb)).status_code, 405)


class AddNoteTests(RookieBoardFixture):
    def url(self, player: Player) -> str:
        return reverse("scouting:add_note", args=[player.pk])

    def test_creates_note_and_shows_count(self) -> None:
        response = self.client.post(self.url(self.qb), {"body": "Elite arm talent"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ScoutingNote.objects.filter(player=self.qb).count(), 1)
        self.assertContains(response, "📝 1")

    def test_blank_note_is_ignored(self) -> None:
        self.client.post(self.url(self.qb), {"body": "   "})
        self.assertFalse(ScoutingNote.objects.filter(player=self.qb).exists())

    def test_get_is_rejected(self) -> None:
        self.assertEqual(self.client.get(self.url(self.qb)).status_code, 405)
