from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


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


class PlayerWeekStat(TimeStampedModel):
    """One player's stat or projection line for a single NFL week.

    Fed by ``manage.py sync_stats`` from Sleeper's parallel ``/stats`` and
    ``/projections`` endpoints. **One table, discriminated by ``kind``**, because
    the two payloads are identically shaped and the ML feature will join a
    projection against the realised stat for the same ``(player, season, week)``
    — one set of indexes, one upsert path, and that join for free. The full
    Sleeper stat-category dict is kept in ``stats`` so a new category never costs
    a migration; the three fantasy scoring totals are promoted to nullable
    columns for sorting and aggregation.
    """

    class Kind(models.TextChoices):
        STAT = "stat", "Stat"
        PROJECTION = "projection", "Projection"

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="week_stats"
    )
    season = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    # Sleeper path segment: regular | post | pre. Default matches the backfill.
    season_type = models.CharField(max_length=8, default="regular")
    kind = models.CharField(max_length=12, choices=Kind.choices)

    pts_ppr = models.FloatField(null=True, blank=True)
    pts_half_ppr = models.FloatField(null=True, blank=True)
    pts_std = models.FloatField(null=True, blank=True)

    # Whole stat-category dict, so a Sleeper schema addition never migrates.
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        # The idempotency key PR 02's bulk upsert writes against. season_type is
        # in the key so a postseason pull can't collide with the regular-season
        # row for the same week.
        unique_together = ("player", "season", "week", "season_type", "kind")
        ordering = ["-season", "-week", "kind"]
        indexes = [
            models.Index(fields=["season", "week", "kind"]),
            models.Index(fields=["player", "kind"]),
            models.Index(fields=["kind", "season", "week"]),
        ]

    def __str__(self) -> str:
        return f"{self.player} {self.season} W{self.week} {self.kind}"


class PlayerSeasonMetrics(TimeStampedModel):
    """Derived, model-ready metrics for one player's season.

    Materialized by ``manage.py recompute_metrics`` from the realised
    ``PlayerWeekStat`` rows (``kind="stat"``) — **deterministic feature
    engineering, NOT a prediction**. One row per ``(player, season,
    season_type)``; the ML valuation in feature 006 joins these against
    ``Player`` for age/position/experience. The full summed usage dict is kept in
    ``usage`` so a new Sleeper stat category never costs a migration; the few
    columns we sort/filter on are promoted (the ``PlayerWeekStat`` pattern).
    """

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="season_metrics"
    )
    season = models.PositiveSmallIntegerField()
    season_type = models.CharField(max_length=8, default="regular")
    # Denormalized from Player so per-season leaderboards filter without a join.
    position = models.CharField(max_length=8, blank=True)

    games_played = models.PositiveSmallIntegerField(default=0)

    # Season totals.
    total_ppr = models.FloatField(null=True, blank=True)
    total_half_ppr = models.FloatField(null=True, blank=True)
    total_std = models.FloatField(null=True, blank=True)

    # Per-game averages (total / games_played).
    ppg_ppr = models.FloatField(null=True, blank=True)
    ppg_half_ppr = models.FloatField(null=True, blank=True)
    ppg_std = models.FloatField(null=True, blank=True)

    # Consistency, all on weekly PPR points.
    stdev_ppr = models.FloatField(null=True, blank=True)  # population stdev
    floor_ppr = models.FloatField(null=True, blank=True)  # min weekly PPR
    ceiling_ppr = models.FloatField(null=True, blank=True)  # max weekly PPR

    # Recent form: average of the last RECENT_WINDOW played weeks, and the delta
    # vs the season average (positive = trending up).
    recent_ppg_ppr = models.FloatField(null=True, blank=True)
    form_delta_ppr = models.FloatField(null=True, blank=True)

    # Usage proxies, summed across the season. Promoted for sorting; the full
    # summed stat-category dict is in `usage`.
    targets = models.PositiveIntegerField(null=True, blank=True)  # rec_tgt
    carries = models.PositiveIntegerField(null=True, blank=True)  # rush_att
    snaps = models.PositiveIntegerField(null=True, blank=True)  # off_snp
    usage = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("player", "season", "season_type")
        ordering = ["-season", "-ppg_ppr"]
        indexes = [
            models.Index(fields=["season", "position"]),
            models.Index(fields=["player", "season"]),
            models.Index(fields=["season", "-ppg_ppr"]),
        ]

    def __str__(self) -> str:
        return f"{self.player} {self.season} metrics"


class PlayerValue(TimeStampedModel):
    """A three-axis dynasty valuation for one player, season, and model.

    Written by ``manage.py recompute_values`` (feature 006 PR 02). ``now_score``
    is this-season lineup value, ``prospect_score`` is breakout likelihood, and
    ``horizon_score`` / ``horizon_seasons`` / ``expires_season`` are the
    expiration axis (how long current form holds). ``value`` is the stored blend
    of the three under the default ``balanced`` weight profile; other profiles
    re-blend the sub-score columns at query time. ``components`` keeps every
    intermediate so the numbers are inspectable and a trained model can store
    feature attributions in the same field. ``model_version`` is on the natural
    key so ``baseline-v1`` and a future ``trained-v1`` coexist for comparison —
    the app reads whichever ``ACTIVE_MODEL_VERSION`` names.
    """

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="values")
    season = models.PositiveSmallIntegerField()
    model_version = models.CharField(max_length=32, default="baseline-v1")

    # Snapshot of the player's position at compute time, so positional rank /
    # tier / scarcity are stable and the overlay never re-joins Player.
    position = models.CharField(max_length=8, blank=True)

    # The three axes, each normalized 0–100 across the scored pool. Columns (not
    # JSON keys) so weight profiles can re-blend them in querysets.
    now_score = models.FloatField(default=0.0)
    prospect_score = models.FloatField(default=0.0)
    horizon_score = models.FloatField(default=0.0)

    # Expiration: expected seasons of remaining form (position age curve), and
    # the human-readable "holds form through" season.
    horizon_seasons = models.FloatField(default=0.0)
    expires_season = models.PositiveSmallIntegerField(null=True, blank=True)

    # Blended dynasty value, 0–100, under WEIGHT_PROFILES["balanced"]. The
    # pre-normalization raw numbers and all factors live in ``components``.
    value = models.FloatField()
    tier = models.PositiveSmallIntegerField(null=True, blank=True)
    position_rank = models.PositiveSmallIntegerField(null=True, blank=True)
    overall_rank = models.PositiveSmallIntegerField(null=True, blank=True)

    # Every intermediate (production, recent-form blend, age multiplier, scarcity
    # weight, market nudge, raw, normalized) — so a value is debuggable and a
    # trained model can drop attributions here.
    components = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("player", "season", "model_version")
        ordering = ["-season", "model_version", "-value"]
        indexes = [
            # The overlay in PR 03: active model, one season, by value desc.
            models.Index(fields=["season", "model_version", "-value"]),
            # Positional ranking / tiering within a season.
            models.Index(fields=["season", "model_version", "position", "-value"]),
            # Per-player lookup across seasons (value history / admin).
            models.Index(fields=["player", "model_version"]),
        ]

    def __str__(self) -> str:
        return f"{self.player} {self.season} {self.model_version} v={self.value:.1f}"
