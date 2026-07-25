from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.leagues.models import League
from apps.players.models import Player
from apps.scouting.models import ScoutingNote, Target


def make_player(
    sleeper_id: str = "1", name: str = "Caleb Williams", **fields: object
) -> Player:
    """A minimal rookie player — no Sleeper calls, no roster required."""
    defaults: dict[str, object] = {
        "full_name": name,
        "position": "QB",
        "team": "CHI",
        "years_exp": 0,
        "rookie_year": 2024,
    }
    defaults.update(fields)
    return Player.objects.create(sleeper_id=sleeper_id, **defaults)


def make_league(slug: str = "the-dynasty", name: str = "The Dynasty") -> League:
    return League.objects.create(name=name, slug=slug, normalized_name=slug)


class TargetModelTests(TestCase):
    def test_target_str(self) -> None:
        target = Target(
            player=make_player(),
            league=make_league(),
            stance=Target.Stance.ACQUIRE,
        )
        self.assertEqual(str(target), "Caleb Williams (QB CHI) — Acquire (The Dynasty)")

    def test_one_stance_per_player_per_league(self) -> None:
        player, league = make_player(), make_league()
        Target.objects.create(player=player, league=league, stance="acquire")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Target.objects.create(player=player, league=league, stance="avoid")

    def test_same_player_targeted_in_two_leagues(self) -> None:
        player = make_player()
        a = make_league("league-a", "League A")
        b = make_league("league-b", "League B")
        Target.objects.create(player=player, league=a, stance="acquire")
        Target.objects.create(player=player, league=b, stance="avoid")
        self.assertEqual(Target.objects.filter(player=player).count(), 2)

    def test_priority_defaults_to_medium(self) -> None:
        target = Target.objects.create(
            player=make_player(), league=make_league(), stance="acquire"
        )
        target.refresh_from_db()
        self.assertEqual(target.priority, Target.Priority.MEDIUM)
        self.assertIsNone(target.tier)

    def test_stance_choices(self) -> None:
        self.assertEqual(Target.Stance.values, ["acquire", "avoid"])

    def test_deleting_player_cascades(self) -> None:
        player = make_player()
        Target.objects.create(player=player, league=make_league(), stance="acquire")
        player.delete()
        self.assertFalse(Target.objects.exists())

    def test_deleting_league_cascades(self) -> None:
        league = make_league()
        Target.objects.create(player=make_player(), league=league, stance="acquire")
        league.delete()
        self.assertFalse(Target.objects.exists())


class ScoutingNoteModelTests(TestCase):
    def test_scouting_note_str_truncates_long_body(self) -> None:
        player = make_player(name="Marvin Harrison Jr.")
        league = make_league()
        short = ScoutingNote(player=player, league=league, body="Elite route runner")
        self.assertEqual(str(short), "Marvin Harrison Jr. (QB CHI): Elite route runner")

        note = ScoutingNote(player=player, league=league, body="x" * 60)
        self.assertTrue(str(note).endswith("..."))
        self.assertIn("x" * 47, str(note))

    def test_notes_default_ordering_newest_first(self) -> None:
        player, league = make_player(), make_league()
        older = ScoutingNote.objects.create(player=player, league=league, body="older")
        newer = ScoutingNote.objects.create(player=player, league=league, body="newer")
        now = timezone.now()
        notes = ScoutingNote.objects
        notes.filter(pk=older.pk).update(created_at=now - timedelta(hours=1))
        notes.filter(pk=newer.pk).update(created_at=now)

        bodies = list(
            ScoutingNote.objects.filter(player=player).values_list("body", flat=True)
        )
        self.assertEqual(bodies, ["newer", "older"])

    def test_deleting_player_cascades(self) -> None:
        player = make_player()
        ScoutingNote.objects.create(player=player, league=make_league(), body="note")
        player.delete()
        self.assertFalse(ScoutingNote.objects.exists())
