from __future__ import annotations

from django.db import models


class Player(models.Model):
    """An NFL player as Sleeper knows them.

    Populated by ``manage.py sync_players``. The table holds only players who
    are actually live (on an NFL roster, in a fantasy-relevant position) plus
    anyone rostered in a tracked league — see ``apps.players.services``.
    """

    sleeper_id = models.CharField(max_length=16, unique=True)

    first_name = models.CharField(max_length=64, blank=True)
    last_name = models.CharField(max_length=64, blank=True)
    full_name = models.CharField(max_length=128, blank=True)
    # Sleeper's own normalised search key: lowercase, punctuation stripped.
    search_full_name = models.CharField(max_length=128, blank=True)

    position = models.CharField(max_length=8, blank=True)
    fantasy_positions = models.JSONField(default=list, blank=True)
    team = models.CharField(max_length=8, blank=True)
    number = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(max_length=32, blank=True)
    # Sleeper's own flag. Stored because it is data, but NOT a liveness signal:
    # Tom Brady and Drew Brees are both active=True. Filter on `team` instead.
    active = models.BooleanField(default=False)

    age = models.PositiveIntegerField(null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    years_exp = models.PositiveIntegerField(null=True, blank=True)
    rookie_year = models.PositiveIntegerField(null=True, blank=True)
    college = models.CharField(max_length=128, blank=True)
    height = models.CharField(max_length=16, blank=True)
    weight = models.CharField(max_length=16, blank=True)

    depth_chart_position = models.CharField(max_length=16, blank=True)
    depth_chart_order = models.PositiveIntegerField(null=True, blank=True)

    injury_status = models.CharField(max_length=64, blank=True)
    injury_body_part = models.CharField(max_length=64, blank=True)

    # Coarse search-ordering hint from Sleeper, NOT an ADP or a ranking: values
    # collide heavily and the 999/9999999 sentinels are normalised to NULL on
    # ingest. Only ever use it as a tiebreak.
    search_rank = models.PositiveIntegerField(null=True, blank=True)

    # Whole payload, so a Sleeper schema addition never costs a migration.
    raw = models.JSONField(default=dict, blank=True)

    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]
        indexes = [
            models.Index(fields=["position"]),
            models.Index(fields=["team"]),
            models.Index(fields=["active"]),
            models.Index(fields=["search_full_name"]),
            models.Index(fields=["position", "team"]),
        ]

    def __str__(self) -> str:
        label = self.full_name or f"{self.first_name} {self.last_name}".strip()
        return f"{label} ({self.position} {self.team})".strip()

    @property
    def is_rookie(self) -> bool:
        return self.years_exp == 0


class TrendingPlayer(models.Model):
    """Most-added / most-dropped counts across all of Sleeper.

    Replaced wholesale on each ``sync_trending`` run — these are a rolling
    window, not history.
    """

    class Kind(models.TextChoices):
        ADD = "add", "Add"
        DROP = "drop", "Drop"

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="trending"
    )
    kind = models.CharField(max_length=8, choices=Kind.choices)
    count = models.PositiveIntegerField(default=0)
    lookback_hours = models.PositiveIntegerField(default=24)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("player", "kind")
        ordering = ["-count"]
        indexes = [models.Index(fields=["kind", "-count"])]

    def __str__(self) -> str:
        return f"{self.player} {self.kind} {self.count}"
