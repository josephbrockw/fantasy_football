from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.players.services import sync_trending
from apps.sleeper.client import SleeperAPIError


class Command(BaseCommand):
    help = "Sync Sleeper's trending add/drop counts for the free agent board."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--lookback-hours",
            type=int,
            default=24,
            help="Rolling window to report on (default: 24).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Players to fetch per direction (default: 100).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            stats = sync_trending(
                lookback_hours=options["lookback_hours"], limit=options["limit"]
            )
        except SleeperAPIError as exc:
            raise CommandError(f"Sleeper sync failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Stored {stats.written} trending row(s); "
                f"skipped {stats.skipped} unknown player(s)."
            )
        )
