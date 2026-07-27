from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.players.valuation import (
    ACTIVE_MODEL_VERSION,
    latest_value_season,
    recompute_values,
)


class Command(BaseCommand):
    help = (
        "Recompute dynasty player values (the three-axis baseline) for a season. "
        "Reads PlayerSeasonMetrics + PlayerProfile; no network. Defaults to the "
        "latest season with metrics."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--season", type=int, default=None)
        parser.add_argument("--model-version", default=ACTIVE_MODEL_VERSION)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        season = options["season"]
        if season is None:
            season = latest_value_season()
            if season is None:
                raise CommandError(
                    "No PlayerSeasonMetrics — run make recompute-metrics first."
                )
        try:
            stats = recompute_values(
                season=season,
                model_version=options["model_version"],
                dry_run=options["dry_run"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {stats.written} value(s) for season {season} "
                f"({options['model_version']})."
            )
        )
