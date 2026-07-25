from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.enrichment.loaders import DynastyProcessLoader, ProfileLoadError
from apps.enrichment.services import sync_profiles


class Command(BaseCommand):
    help = (
        "Enrich players with external draft capital and crosswalk ids from the "
        "DynastyProcess db_playerids release. Rows that don't map to a tracked "
        "player are skipped and counted."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--url", default="", help="Override the db_playerids URL (pin a version)."
        )
        parser.add_argument(
            "--source",
            choices=["ids", "combine", "both"],
            default="both",
            help="Which release(s) to sync (default: both).",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        loader = DynastyProcessLoader(url=options["url"]) if options["url"] else None
        source = options["source"]
        sources = ("ids", "combine") if source == "both" else (source,)
        try:
            stats = sync_profiles(
                loader=loader, dry_run=options["dry_run"], sources=sources
            )
        except ProfileLoadError as exc:
            raise CommandError(f"Profile sync failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {stats.written} profile(s); skipped {stats.skipped} unmatched."
            )
        )
