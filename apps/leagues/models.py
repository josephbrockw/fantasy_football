from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel
from apps.players.models import Player


class SleeperAccount(TimeStampedModel):
    """My own Sleeper identity.

    Stores ``sleeper_user_id`` as the durable key — Sleeper usernames are
    mutable, user ids are not.
    """

    username = models.CharField(max_length=64)
    sleeper_user_id = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=64, blank=True)
    avatar = models.CharField(max_length=64, blank=True)

    def __str__(self) -> str:
        return f"{self.username} ({self.sleeper_user_id})"


class League(TimeStampedModel):
    """A dynasty league as a permanent thing, across all its seasons.

    Sleeper mints a brand-new ``league_id`` every season, so the Sleeper league
    is modelled as ``LeagueSeason`` and this row is what persists. Seasons are
    tied together by walking ``previous_league_id``, falling back to matching
    ``normalized_name`` when that chain is broken.
    """

    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=140, unique=True)
    # Casefolded, non-alphanumerics stripped. The fallback match key.
    normalized_name = models.CharField(max_length=128, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def current_season(self) -> LeagueSeason | None:
        return self.seasons.order_by("-season").first()


class LeagueSeason(TimeStampedModel):
    """One Sleeper league — that is, one season of a ``League``."""

    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name="seasons")
    season = models.CharField(max_length=8)
    sleeper_league_id = models.CharField(max_length=32, unique=True)
    previous_sleeper_league_id = models.CharField(
        max_length=32, blank=True, db_index=True
    )

    status = models.CharField(max_length=32, blank=True)
    total_rosters = models.PositiveIntegerField(default=0)

    roster_positions = models.JSONField(default=list, blank=True)
    scoring_settings = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-season"]
        unique_together = ("league", "season")

    def __str__(self) -> str:
        return f"{self.league.name} {self.season}"

    @property
    def fantasy_positions(self) -> list[str]:
        """Distinct positions this league starts, minus the flex placeholders.

        Deduplicated: ``roster_positions`` lists a slot per starter, so a
        league starting two RBs has "RB" twice. Callers want the position set.
        """
        placeholders = {
            "BN",
            "IR",
            "TAXI",
            "FLEX",
            "SUPER_FLEX",
            "IDP_FLEX",
            "REC_FLEX",
            "WRRB_FLEX",
        }
        seen: list[str] = []
        for position in self.roster_positions:
            if position not in placeholders and position not in seen:
                seen.append(position)
        return seen


class Manager(TimeStampedModel):
    """A person who manages a team.

    Keyed on ``sleeper_user_id``, which is stable across seasons — this is what
    links a rival's 2026 roster to their 2027 one.
    """

    sleeper_user_id = models.CharField(max_length=32, unique=True)
    username = models.CharField(max_length=64, blank=True)
    display_name = models.CharField(max_length=64, blank=True)
    avatar = models.CharField(max_length=64, blank=True)
    is_me = models.BooleanField(default=False)

    class Meta:
        ordering = ["display_name"]

    def __str__(self) -> str:
        return self.display_name or self.username or self.sleeper_user_id


class Team(TimeStampedModel):
    """One manager's roster within one season.

    ``roster_id`` is only unique *within* a ``LeagueSeason`` and can change
    between seasons — never use it as a cross-season key. Use ``manager``
    (i.e. ``sleeper_user_id``) for that.
    """

    league_season = models.ForeignKey(
        LeagueSeason, on_delete=models.CASCADE, related_name="teams"
    )
    roster_id = models.PositiveIntegerField()
    # Null for an orphan roster — real leagues have rosters with no owner.
    manager = models.ForeignKey(
        Manager,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teams",
    )
    team_name = models.CharField(max_length=128, blank=True)

    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    ties = models.PositiveIntegerField(default=0)
    points_for = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    points_against = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    waiver_budget_used = models.PositiveIntegerField(default=0)

    players: models.ManyToManyField = models.ManyToManyField(
        Player, through="RosterSlot", related_name="teams"
    )

    class Meta:
        ordering = ["-wins", "-points_for"]
        unique_together = ("league_season", "roster_id")

    def __str__(self) -> str:
        label = self.team_name or (self.manager.display_name if self.manager else "")
        return label or f"Roster {self.roster_id}"

    @property
    def record(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base


class RosterSlot(TimeStampedModel):
    """Where a player sits on a team: starting, benched, taxi, or IR.

    For starters, ``lineup_order`` is the index into Sleeper's ``starters``
    array, which aligns positionally with the league's starting slots — so
    index 8 of a ``[QB, RB, RB, WR, WR, TE, FLEX, FLEX, SUPER_FLEX, DEF]``
    lineup is the SUPER_FLEX. ``lineup_position`` caches that resolved label.
    Without these the lineup is meaningless: a legal two-QB SUPER_FLEX roster
    just looks like two quarterbacks started at once.
    """

    class Slot(models.TextChoices):
        STARTER = "starter", "Starter"
        BENCH = "bench", "Bench"
        TAXI = "taxi", "Taxi"
        IR = "ir", "IR"

    team = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name="roster_slots"
    )
    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="roster_slots"
    )
    slot = models.CharField(max_length=16, choices=Slot.choices, default=Slot.BENCH)

    # Starters only; blank/null for bench, taxi, and IR.
    lineup_position = models.CharField(max_length=16, blank=True)
    lineup_order = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("team", "player")
        ordering = ["slot", "lineup_order"]
        indexes = [models.Index(fields=["team", "slot"])]

    def __str__(self) -> str:
        if self.lineup_position:
            return f"{self.player} — {self.lineup_position}"
        return f"{self.player} — {self.slot}"


class Trade(TimeStampedModel):
    """A completed trade within a single ``LeagueSeason``.

    Hangs off the season, not the permanent ``League``, because a trade happens
    between rosters within one season. ``sleeper_transaction_id`` is the
    idempotency key the sync upserts on.
    """

    league_season = models.ForeignKey(
        LeagueSeason, on_delete=models.CASCADE, related_name="trades"
    )
    sleeper_transaction_id = models.CharField(max_length=32, unique=True)
    week = models.PositiveIntegerField()
    # Stored verbatim (Sleeper sends "complete") rather than assuming the value.
    status = models.CharField(max_length=16, blank=True)
    # Sleeper's status_updated is epoch milliseconds; the sync converts it to an
    # aware datetime before storing.
    status_updated = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-status_updated"]
        indexes = [models.Index(fields=["league_season", "-status_updated"])]

    def __str__(self) -> str:
        return f"Trade {self.sleeper_transaction_id} (wk {self.week})"


class TradeAsset(TimeStampedModel):
    """One asset moving in a trade: a player, a draft pick, or FAAB.

    Sleeper spreads a trade across three parallel structures (``adds``/``drops``
    map ``player_id`` → ``roster_id``, ``draft_picks`` is a list,
    ``waiver_budget`` is FAAB); this models their union with a ``kind``
    discriminator rather than three tables. The sending/receiving *manager* is
    reachable via ``from_team.manager`` / ``to_team.manager`` — ``Team`` is the
    right grain because a trade is season-scoped.
    """

    class Kind(models.TextChoices):
        PLAYER = "player", "Player"
        PICK = "pick", "Pick"
        FAAB = "faab", "FAAB"

    trade = models.ForeignKey(Trade, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    # PROTECT, not CASCADE: deleting a player must never silently erase trade
    # history.
    player = models.ForeignKey(
        Player,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="trade_assets",
    )
    pick_season = models.CharField(max_length=8, blank=True)
    pick_round = models.PositiveIntegerField(null=True, blank=True)
    faab_amount = models.PositiveIntegerField(null=True, blank=True)
    from_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets_sent",
    )
    to_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets_received",
    )

    class Meta:
        ordering = ["kind"]

    @property
    def label(self) -> str:
        """A kind-agnostic label, so templates don't branch on ``kind``."""
        if self.kind == self.Kind.PLAYER:
            return str(self.player) if self.player_id else "—"
        if self.kind == self.Kind.PICK:
            return f"{self.pick_season} R{self.pick_round} pick"
        if self.kind == self.Kind.FAAB:
            return f"${self.faab_amount} FAAB"
        return "—"

    def __str__(self) -> str:
        return self.label


class TradedPick(TimeStampedModel):
    """Current ownership of a future draft pick (a snapshot, not history).

    The snapshot Sleeper returns from ``/traded_picks``, rebuilt wholesale on
    each sync (like ``RosterSlot`` / ``TrendingPlayer`` are replaced, not
    diffed). The pick's ``season`` is often a future year with no
    ``LeagueSeason`` row yet, which is exactly why owners key on ``Manager`` (the
    cross-season-stable ``sleeper_user_id``), not ``Team``: ``roster_id`` is
    season-scoped and never a cross-season key.
    """

    league_season = models.ForeignKey(
        LeagueSeason, on_delete=models.CASCADE, related_name="traded_picks"
    )
    # The pick's own season, e.g. "2027" — often a future year.
    season = models.CharField(max_length=8)
    round = models.PositiveIntegerField()
    original_owner = models.ForeignKey(
        Manager, on_delete=models.CASCADE, related_name="picks_originally_owned"
    )
    current_owner = models.ForeignKey(
        Manager, on_delete=models.CASCADE, related_name="picks_owned"
    )

    class Meta:
        # A pick is identified by whose it originally was.
        unique_together = ("league_season", "season", "round", "original_owner")
        ordering = ["season", "round"]
        indexes = [models.Index(fields=["league_season", "season"])]

    def __str__(self) -> str:
        return f"{self.season} R{self.round} pick"
