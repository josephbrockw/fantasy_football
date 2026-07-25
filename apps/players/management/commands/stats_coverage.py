from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.players.models import PlayerWeekStat


class Command(BaseCommand):
    help = (
        "Print how many stat and projection rows the backfill has stored, per "
        "season and week — a read-only verification aid for `make sync-stats`."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--season",
            default="",
            help="Comma-separated seasons to restrict to; default all.",
        )
        parser.add_argument("--season-type", default="regular")

    def handle(self, *args: Any, **options: Any) -> None:
        rows = PlayerWeekStat.objects.filter(season_type=options["season_type"])
        if options["season"]:
            seasons = [int(s) for s in options["season"].split(",") if s.strip()]
            rows = rows.filter(season__in=seasons)

        # One grouped query, folded into {season: {week: {kind: count}}}.
        grouped = (
            rows.values("season", "week", "kind")
            .annotate(n=Count("id"))
            .order_by("season", "week", "kind")
        )
        counts: dict[int, dict[int, dict[str, int]]] = {}
        for row in grouped:
            week_counts = counts.setdefault(row["season"], {}).setdefault(
                row["week"], {}
            )
            week_counts[row["kind"]] = row["n"]

        if not counts:
            self.stdout.write(
                self.style.WARNING(
                    "No PlayerWeekStat rows found — run make sync-stats."
                )
            )
            return

        for season in sorted(counts):
            weeks = counts[season]
            self.stdout.write(
                self.style.SUCCESS(f"{season} ({options['season_type']})")
            )
            total = 0
            for week in sorted(weeks):
                stat = weeks[week].get(PlayerWeekStat.Kind.STAT, 0)
                proj = weeks[week].get(PlayerWeekStat.Kind.PROJECTION, 0)
                total += stat + proj
                self.stdout.write(f"  W{week:02d}  stat={stat}  proj={proj}")
            self.stdout.write(f"  {len(weeks)} week(s), {total} row(s)")
