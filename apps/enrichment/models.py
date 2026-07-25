from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class PlayerProfile(TimeStampedModel):
    """External, non-Sleeper enrichment for a ``Player``.

    Populated from versioned CSV release files (DynastyProcess ``db_playerids``
    for draft capital + the ``sleeper_id`` crosswalk; nflverse combine for
    athleticism) — NOT from Sleeper and NOT by scraping. One row per player.
    Draft capital is the strongest dynasty signal for young/unproven players and
    feeds features 005–007. All measurables are nullable — the combine covers
    only a subset of players.
    """

    player = models.OneToOneField(
        "players.Player", on_delete=models.CASCADE, related_name="profile"
    )

    # --- NFL draft capital (DynastyProcess db_playerids) ---
    draft_year = models.PositiveSmallIntegerField(null=True, blank=True)
    draft_round = models.PositiveSmallIntegerField(null=True, blank=True)
    # Overall pick number (draft_ovr), not round-relative.
    draft_pick = models.PositiveSmallIntegerField(null=True, blank=True)
    draft_team = models.CharField(max_length=8, blank=True)

    # --- external crosswalk ids (for re-joining other releases) ---
    pfr_id = models.CharField(max_length=16, blank=True)  # combine join key
    gsis_id = models.CharField(max_length=16, blank=True)

    # --- athleticism / combine measurables (nflverse combine, PR 03) ---
    height_inches = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_lbs = models.PositiveSmallIntegerField(null=True, blank=True)
    bmi = models.FloatField(null=True, blank=True)
    forty = models.FloatField(null=True, blank=True)
    bench = models.PositiveSmallIntegerField(null=True, blank=True)
    vertical = models.FloatField(null=True, blank=True)
    broad_jump = models.PositiveSmallIntegerField(null=True, blank=True)
    cone = models.FloatField(null=True, blank=True)
    shuttle = models.FloatField(null=True, blank=True)

    # Which release(s) last touched this row — cheap provenance for debugging.
    source = models.CharField(max_length=32, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["draft_year"]),
            models.Index(fields=["draft_round"]),
            models.Index(fields=["pfr_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.player} profile"

    @property
    def draft_capital_label(self) -> str:
        """e.g. '2023 R1.05' — a compact draft-capital badge for later UI."""
        if not self.draft_year:
            return ""
        if self.draft_round and self.draft_pick:
            return f"{self.draft_year} R{self.draft_round}.{self.draft_pick:02d}"
        return str(self.draft_year)
