from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class SyncRun(TimeStampedModel):
    """Audit record for one run of a sync command.

    Two jobs: make failures visible after the fact, and give the player sync a
    place to check when it last succeeded (Sleeper asks that the ~15 MB player
    dump be fetched at most once a day).
    """

    class Kind(models.TextChoices):
        PLAYERS = "players", "Players"
        LEAGUE = "league", "League"
        TRENDING = "trending", "Trending"
        TRANSACTIONS = "transactions", "Transactions"
        STATS = "stats", "Stats"
        PROFILES = "profiles", "Profiles"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    records_written = models.PositiveIntegerField(default=0)
    records_skipped = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["kind", "status", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.kind} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"

    @classmethod
    def last_success(cls, kind: str) -> SyncRun | None:
        return (
            cls.objects.filter(kind=kind, status=cls.Status.SUCCESS)
            .order_by("-finished_at")
            .first()
        )

    def mark_success(self, written: int = 0, skipped: int = 0) -> None:
        self.status = self.Status.SUCCESS
        self.records_written = written
        self.records_skipped = skipped
        self.finished_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "records_written",
                "records_skipped",
                "finished_at",
                "updated_at",
            ]
        )

    def mark_failed(self, error: str) -> None:
        self.status = self.Status.FAILED
        # Keep the column bounded; a full traceback is not worth unbounded rows.
        self.error = error[:4000]
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error", "finished_at", "updated_at"])

    @classmethod
    @contextmanager
    def track(cls, kind: str) -> Iterator[SyncRun]:
        """Run a sync, recording success or failure either way.

        The body sets ``records_written`` / ``records_skipped`` on the yielded
        run; they are persisted on a clean exit. Exceptions are recorded and
        re-raised.
        """
        run = cls.objects.create(kind=kind)
        try:
            yield run
        except Exception as exc:
            run.mark_failed(f"{type(exc).__name__}: {exc}")
            raise
        run.mark_success(run.records_written, run.records_skipped)
