from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

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


class TargetModelTests(TestCase):
    def test_target_str(self) -> None:
        target = Target(player=make_player(), stance=Target.Stance.ACQUIRE)
        self.assertEqual(str(target), "Caleb Williams (QB CHI) — Acquire")

    def test_target_is_one_to_one_per_player(self) -> None:
        player = make_player()
        Target.objects.create(player=player, stance=Target.Stance.ACQUIRE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Target.objects.create(player=player, stance=Target.Stance.AVOID)

    def test_target_priority_defaults_to_medium(self) -> None:
        target = Target.objects.create(
            player=make_player(), stance=Target.Stance.ACQUIRE
        )
        target.refresh_from_db()
        self.assertEqual(target.priority, Target.Priority.MEDIUM)
        self.assertIsNone(target.tier)

    def test_target_stance_choices(self) -> None:
        self.assertEqual(Target.Stance.values, ["acquire", "avoid"])

    def test_deleting_player_cascades_to_target(self) -> None:
        player = make_player()
        Target.objects.create(player=player, stance=Target.Stance.ACQUIRE)
        player.delete()
        self.assertFalse(Target.objects.exists())


class ScoutingNoteModelTests(TestCase):
    def test_scouting_note_str_truncates_long_body(self) -> None:
        player = make_player(name="Marvin Harrison Jr.")
        short = ScoutingNote(player=player, body="Elite route runner")
        self.assertEqual(str(short), "Marvin Harrison Jr. (QB CHI): Elite route runner")

        long_body = "x" * 60
        note = ScoutingNote(player=player, body=long_body)
        self.assertTrue(str(note).endswith("..."))
        self.assertIn("x" * 47, str(note))

    def test_scouting_notes_default_ordering_newest_first(self) -> None:
        player = make_player()
        older = ScoutingNote.objects.create(player=player, body="older")
        newer = ScoutingNote.objects.create(player=player, body="newer")
        # auto_now_add fills created_at at insert; pin explicit distinct values so
        # ordering is deterministic regardless of clock resolution.
        now = timezone.now()
        notes = ScoutingNote.objects
        notes.filter(pk=older.pk).update(created_at=now - timedelta(hours=1))
        notes.filter(pk=newer.pk).update(created_at=now)

        # Meta.ordering applies to the default queryset, so no explicit order_by.
        bodies = list(
            ScoutingNote.objects.filter(player=player).values_list("body", flat=True)
        )
        self.assertEqual(bodies, ["newer", "older"])

    def test_deleting_player_cascades_to_notes(self) -> None:
        player = make_player()
        ScoutingNote.objects.create(player=player, body="note")
        player.delete()
        self.assertFalse(ScoutingNote.objects.exists())
