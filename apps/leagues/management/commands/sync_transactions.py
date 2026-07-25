from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.leagues.transactions import sync_transactions
from apps.sleeper.client import SleeperAPIError


class Command(BaseCommand):
    help = (
        "Sync completed trades and traded draft picks from Sleeper. Requires a "
        "prior `make sync-league` — it maps roster_ids through synced Teams to "
        "managers."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--season",
            default="",
            help="Season to sync, e.g. 2026. Defaults to all synced seasons.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            stats = sync_transactions(season=options["season"])
        except SleeperAPIError as exc:
            raise CommandError(f"Transaction sync failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {stats.trades} trade(s), {stats.assets} asset(s), "
                f"{stats.picks} traded pick(s)."
            )
        )
        if stats.skipped:
            self.stdout.write(
                f"Skipped {stats.skipped} asset(s)/pick(s) with a missing "
                f"player or roster."
            )
