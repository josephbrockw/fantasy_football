from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.players.services import sync_players
from apps.sleeper.client import SleeperAPIError
from apps.sleeper.models import SyncRun

# Sleeper asks that the ~15 MB player dump be fetched at most once a day.
MIN_INTERVAL = timedelta(hours=24)


class Command(BaseCommand):
    help = (
        "Sync the Sleeper player universe. Stores only live players "
        "(on an NFL roster, in a fantasy position) unless --include-inactive."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if a sync succeeded within the last 24 hours.",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Skip the live-player filter and store every record.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and filter, but write nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["force"] and self._synced_recently():
            self.stdout.write(
                self.style.WARNING(
                    "Players were synced within the last 24 hours; skipping. "
                    "Use --force to override."
                )
            )
            return

        try:
            stats = sync_players(
                include_inactive=options["include_inactive"],
                dry_run=options["dry_run"],
            )
        except SleeperAPIError as exc:
            raise CommandError(f"Sleeper sync failed: {exc}") from exc

        verb = "Would write" if options["dry_run"] else "Wrote"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {stats.written} players ({stats.skipped} skipped as not live)."
            )
        )

    @staticmethod
    def _synced_recently() -> bool:
        last = SyncRun.last_success(SyncRun.Kind.PLAYERS)
        if last is None or last.finished_at is None:
            return False
        return timezone.now() - last.finished_at < MIN_INTERVAL
