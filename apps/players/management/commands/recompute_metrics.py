from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.players.services import recompute_metrics


class Command(BaseCommand):
    help = (
        "Rebuild PlayerSeasonMetrics from ingested PlayerWeekStat rows. Reads the "
        "local DB only (no network). With no --season it recomputes every season "
        "present in the stats."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--season", type=int, default=None, help="One season; default all present."
        )
        parser.add_argument("--season-type", default="regular")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        season = options["season"]
        stats = recompute_metrics(
            seasons=[season] if season is not None else None,
            season_type=options["season_type"],
            dry_run=options["dry_run"],
        )
        self.stdout.write(
            self.style.SUCCESS(f"Recomputed {stats.written} season-metric row(s).")
        )
