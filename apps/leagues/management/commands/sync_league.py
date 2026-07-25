from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.leagues.services import sync_leagues
from apps.sleeper.client import SleeperAPIError


class Command(BaseCommand):
    help = (
        "Sync leagues, managers, teams, and rosters from Sleeper. Follows "
        "previous_league_id back through prior seasons of the same dynasty."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--username",
            default="",
            help="Sleeper username. Defaults to settings.SLEEPER_USERNAME.",
        )
        parser.add_argument(
            "--season",
            default="",
            help="Season to sync, e.g. 2026. Defaults to the current NFL season.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        username = options["username"] or settings.SLEEPER_USERNAME
        if not username:
            raise CommandError(
                "No Sleeper username. Set SLEEPER_USERNAME in .env or pass --username."
            )

        try:
            stats = sync_leagues(username=username, season=options["season"])
        except SleeperAPIError as exc:
            raise CommandError(f"Sleeper sync failed: {exc}") from exc

        if not stats.seasons:
            self.stdout.write(
                self.style.WARNING(f"No leagues found for {username!r} in that season.")
            )
            return

        for name in stats.league_names:
            self.stdout.write(f"  · {name}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {stats.leagues} league(s), {stats.seasons} season(s), "
                f"{stats.teams} team(s), {stats.roster_slots} roster slot(s)."
            )
        )
        if stats.players_backfilled:
            self.stdout.write(
                f"Backfilled {stats.players_backfilled} player(s) that the "
                f"live-player filter had excluded."
            )
