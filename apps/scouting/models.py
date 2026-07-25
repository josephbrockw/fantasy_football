from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel
from apps.players.models import Player


class Target(TimeStampedModel):
    """My stance on a single player — someone to acquire or avoid.

    ``OneToOne`` because there is exactly one stance per player: setting a stance
    is an ``update_or_create`` and clearing it is a delete. ``notes`` is a single
    quick summary that rides on the target row; longer, dated observations live in
    :class:`ScoutingNote`. Single-user app, so a target is implicitly mine — no
    per-user scoping beyond the existing ``Manager.is_me`` the Targets board uses
    only to label my roster versus a rival's.
    """

    class Stance(models.TextChoices):
        ACQUIRE = "acquire", "Acquire"
        AVOID = "avoid", "Avoid"

    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    player = models.OneToOneField(
        Player, on_delete=models.CASCADE, related_name="target"
    )
    stance = models.CharField(max_length=8, choices=Stance.choices)
    tier = models.PositiveSmallIntegerField(null=True, blank=True)  # 1 = top tier
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.MEDIUM
    )
    notes = models.TextField(blank=True)  # short summary note

    def __str__(self) -> str:
        return f"{self.player} — {self.get_stance_display()}"


class ScoutingNote(TimeStampedModel):
    """A dated, free-form observation about a player.

    A one-to-many log (many notes per player), distinct from ``Target.notes`` and
    independent of whether the player is a target at all — I can scout a prospect
    before deciding a stance.
    """

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="scouting_notes"
    )
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]  # newest first

    def __str__(self) -> str:
        preview = self.body if len(self.body) <= 50 else f"{self.body[:47]}..."
        return f"{self.player}: {preview}"
