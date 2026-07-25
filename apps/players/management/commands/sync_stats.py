from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.players.models import PlayerWeekStat
from apps.players.services import sync_stats
from apps.sleeper.client import SleeperAPIError


class Command(BaseCommand):
    help = (
        "Backfill weekly player stats and projections from Sleeper. With no "
        "arguments it runs a full historical backfill (earliest season through "
        "the current one, weeks 1-18, both stats and projections)."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--start-season", type=int, default=None)
        parser.add_argument("--end-season", type=int, default=None)
        parser.add_argument(
            "--season",
            type=int,
            default=None,
            help="A single season; sets both bounds.",
        )
        parser.add_argument(
            "--week", default="", help="Comma-separated weeks; default is 1-18."
        )
        parser.add_argument(
            "--kind", choices=["stat", "projection", "both"], default="both"
        )
        parser.add_argument("--season-type", default="regular")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        start = options["start_season"]
        end = options["end_season"]
        if options["season"] is not None:
            start = end = options["season"]

        weeks = None
        if options["week"]:
            weeks = [int(w) for w in options["week"].split(",") if w.strip()]

        if options["kind"] == "both":
            kinds: tuple[str, ...] = (
                PlayerWeekStat.Kind.STAT,
                PlayerWeekStat.Kind.PROJECTION,
            )
        else:
            kinds = (options["kind"],)

        try:
            stats = sync_stats(
                start_season=start,
                end_season=end,
                weeks=weeks,
                kinds=kinds,
                season_type=options["season_type"],
                dry_run=options["dry_run"],
            )
        except SleeperAPIError as exc:
            raise CommandError(f"Stats sync failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {stats.written} stat row(s); "
                f"skipped {stats.skipped} unknown/no-data."
            )
        )
