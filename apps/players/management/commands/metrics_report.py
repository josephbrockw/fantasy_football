from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.players.models import PlayerSeasonMetrics


class Command(BaseCommand):
    help = (
        "Print stored PlayerSeasonMetrics per season — row count and the top "
        "players by ppg_ppr. A read-only verification aid for recompute_metrics."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--season", default="", help="Comma-separated seasons; default all."
        )
        parser.add_argument("--season-type", default="regular")
        parser.add_argument(
            "--position", default="", help="Restrict the leaderboard to one position."
        )
        parser.add_argument("--top", type=int, default=10)

    def handle(self, *args: Any, **options: Any) -> None:
        rows = PlayerSeasonMetrics.objects.filter(season_type=options["season_type"])
        if options["season"]:
            wanted = [int(s) for s in options["season"].split(",") if s.strip()]
            rows = rows.filter(season__in=wanted)
        if options["position"]:
            rows = rows.filter(position=options["position"])

        # order_by() clears the model's Meta.ordering; without it the ordering
        # columns join the SELECT and DISTINCT dedupes (season, ppg_ppr) pairs,
        # so a season would repeat once per distinct ppg_ppr.
        seasons = sorted(
            rows.order_by().values_list("season", flat=True).distinct(), reverse=True
        )
        if not seasons:
            self.stdout.write(
                self.style.WARNING(
                    "No PlayerSeasonMetrics rows found — run make recompute-metrics."
                )
            )
            return

        top = options["top"]
        for season in seasons:
            season_rows = rows.filter(season=season)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{season} ({options['season_type']}) — "
                    f"{season_rows.count()} row(s)"
                )
            )
            leaders = (
                season_rows.filter(ppg_ppr__isnull=False)
                .select_related("player")
                .order_by("-ppg_ppr")[:top]
            )
            for rank, m in enumerate(leaders, start=1):
                delta = m.form_delta_ppr if m.form_delta_ppr is not None else 0.0
                self.stdout.write(
                    f"  {rank:2d}. {m.player.full_name:24s} {m.position:3s} "
                    f"{m.games_played:2d}g  ppg={m.ppg_ppr:.1f}  Δ{delta:+.1f}"
                )
