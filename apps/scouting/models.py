from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel
from apps.leagues.models import League
from apps.players.models import Player


class Target(TimeStampedModel):
    """My stance on a player *within one league* — someone to acquire or avoid.

    Scoped to a ``League`` (the permanent record, so it survives Sleeper minting
    a new ``league_id`` each season) because a target only means something
    relative to a specific league's rosters: the same player can be a Tier 1
    acquire in one league and untargeted in another. ``unique_together`` keeps it
    to one stance per ``(player, league)`` — setting one is an
    ``update_or_create`` and clearing it is a delete. ``notes`` is a quick
    summary; longer, dated observations are :class:`ScoutingNote`.
    """

    class Stance(models.TextChoices):
        ACQUIRE = "acquire", "Acquire"
        AVOID = "avoid", "Avoid"

    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="targets")
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="targets")
    stance = models.CharField(max_length=8, choices=Stance.choices)
    tier = models.PositiveSmallIntegerField(null=True, blank=True)  # 1 = top tier
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.MEDIUM
    )
    notes = models.TextField(blank=True)  # short summary note

    class Meta:
        unique_together = ("player", "league")
        ordering = ["stance", "tier", "priority"]

    def __str__(self) -> str:
        return f"{self.player} — {self.get_stance_display()} ({self.league})"


class ScoutingNote(TimeStampedModel):
    """A dated, free-form observation about a player within one league.

    League-scoped like :class:`Target` so a scouting board stays in the context
    of the league you're working. A one-to-many log, independent of whether a
    stance is set.
    """

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="scouting_notes"
    )
    league = models.ForeignKey(
        League, on_delete=models.CASCADE, related_name="scouting_notes"
    )
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]  # newest first

    def __str__(self) -> str:
        preview = self.body if len(self.body) <= 50 else f"{self.body[:47]}..."
        return f"{self.player}: {preview}"
