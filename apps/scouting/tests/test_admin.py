from __future__ import annotations

from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from apps.scouting.admin import ScoutingNoteAdmin
from apps.scouting.models import ScoutingNote
from apps.scouting.tests.test_models import make_player


class ScoutingNoteAdminTests(TestCase):
    def setUp(self) -> None:
        self.admin = ScoutingNoteAdmin(ScoutingNote, AdminSite())

    def test_short_body_passes_through_short_text(self) -> None:
        note = ScoutingNote(player=make_player(), body="Quick note")
        self.assertEqual(self.admin.short_body(note), "Quick note")

    def test_short_body_truncates_long_text(self) -> None:
        note = ScoutingNote(player=make_player(), body="x" * 80)
        result = self.admin.short_body(note)
        self.assertTrue(result.endswith("..."))
        self.assertEqual(len(result), 60)
