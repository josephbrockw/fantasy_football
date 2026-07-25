"""Syncing external per-player enrichment (draft capital + crosswalk ids).

Downloads the DynastyProcess ``db_playerids`` release and maps each row to a
``Player`` by ``sleeper_id``. Rows that don't resolve to a tracked player are
**skipped and counted** — the file is keyed by the whole football universe while
our ``Player`` table holds only live/rostered players, so a source like this must
never fail on a missing FK (the same pattern as ``sync_stats`` / ``sync_trending``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from apps.enrichment.loaders import DynastyProcessLoader, ProfileSource
from apps.enrichment.models import PlayerProfile
from apps.players.models import Player
from apps.sleeper.models import SyncRun

BATCH_SIZE = 500

# Only the draft/id columns this release owns — restricting the upsert to these
# means a later db_playerids refresh never clobbers combine data PR 03 wrote.
PROFILE_DRAFT_UPDATE_FIELDS = [
    "draft_year",
    "draft_round",
    "draft_pick",
    "draft_team",
    "pfr_id",
    "gsis_id",
    "source",
    "updated_at",
]

# Combine measurables (nflverse) — the combine pass writes only these, so it
# never disturbs the draft capital the db_playerids pass wrote.
PROFILE_COMBINE_UPDATE_FIELDS = [
    "height_inches",
    "weight_lbs",
    "bmi",
    "forty",
    "bench",
    "vertical",
    "broad_jump",
    "cone",
    "shuttle",
    "updated_at",
]


@dataclass
class SyncStats:
    written: int = 0
    skipped: int = 0


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        # CSV numerics may arrive as "1" or "1.0"; float() then int() takes both.
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _as_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _height_to_inches(value: Any) -> int | None:
    """Parse nflverse height — ``"6-2"`` / ``"6'2"`` / ``"74"`` → ``74`` inches."""
    text = _as_str(value)
    if not text:
        return None
    for sep in ("-", "'"):
        if sep in text:
            feet, _, inches = text.partition(sep)
            f, i = _as_int(feet), _as_int(inches)
            return f * 12 + i if f is not None and i is not None else None
    return _as_int(text)


def _bmi(height_in: int | None, weight_lb: int | None) -> float | None:
    if not height_in or not weight_lb:
        return None
    return round(weight_lb / (height_in**2) * 703, 1)


def profile_from_row(player: Player, row: dict[str, str]) -> PlayerProfile:
    """Build an unsaved ``PlayerProfile`` from one db_playerids row.

    ``updated_at`` is set explicitly because ``bulk_create(update_conflicts=True)``
    bypasses ``TimeStampedModel``'s ``auto_now``. Combine measurables are left
    untouched here — PR 03 fills them, and they're absent from the update fields.
    """
    return PlayerProfile(
        player=player,
        draft_year=_as_int(row.get("draft_year")),
        draft_round=_as_int(row.get("draft_round")),
        draft_pick=_as_int(row.get("draft_ovr") or row.get("draft_pick")),
        draft_team=_as_str(row.get("draft_team")),
        pfr_id=_as_str(row.get("pfr_id")),
        gsis_id=_as_str(row.get("gsis_id")),
        source="db_playerids",
        updated_at=timezone.now(),
    )


def upsert_profiles(rows: list[PlayerProfile]) -> int:
    """Insert-or-update profiles on the OneToOne ``player`` key. Count written."""
    if not rows:
        return 0
    PlayerProfile.objects.bulk_create(
        rows,
        batch_size=BATCH_SIZE,
        update_conflicts=True,
        unique_fields=["player"],
        update_fields=PROFILE_DRAFT_UPDATE_FIELDS,
    )
    return len(rows)


def _sync_draft_capital(loader: ProfileSource, *, dry_run: bool) -> tuple[int, int]:
    """Draft capital + crosswalk ids from db_playerids, joined by ``sleeper_id``."""
    rows = loader.fetch_player_ids()
    ids = [_as_str(row.get("sleeper_id")) for row in rows]
    known = {
        player.sleeper_id: player
        for player in Player.objects.filter(sleeper_id__in=[sid for sid in ids if sid])
    }
    profiles: list[PlayerProfile] = []
    skipped = 0
    for row in rows:
        player = known.get(_as_str(row.get("sleeper_id")))
        if player is None:
            skipped += 1
            continue
        profiles.append(profile_from_row(player, row))
    written = len(profiles) if dry_run else upsert_profiles(profiles)
    return written, skipped


def _set_combine(profile: PlayerProfile, row: dict[str, str]) -> None:
    height = _height_to_inches(row.get("ht"))
    weight = _as_int(row.get("wt"))
    profile.height_inches = height
    profile.weight_lbs = weight
    profile.bmi = _bmi(height, weight)
    profile.forty = _as_float(row.get("forty"))
    profile.bench = _as_int(row.get("bench"))
    profile.vertical = _as_float(row.get("vertical"))
    profile.broad_jump = _as_int(row.get("broad_jump"))
    profile.cone = _as_float(row.get("cone"))
    profile.shuttle = _as_float(row.get("shuttle"))


def _sync_combine(loader: ProfileSource, *, dry_run: bool) -> tuple[int, int]:
    """Combine athleticism, joined onto existing profiles by ``pfr_id``.

    Update-only: the combine file has no ``sleeper_id``, so it can only enrich
    profiles the draft pass already matched. Rows for an untracked ``pfr_id`` are
    skipped and counted.
    """
    by_pfr = {r["pfr_id"]: r for r in loader.fetch_combine() if r.get("pfr_id")}
    known = {p.pfr_id: p for p in PlayerProfile.objects.filter(pfr_id__in=by_pfr)}
    updates: list[PlayerProfile] = []
    skipped = 0
    now = timezone.now()
    for pfr_id, row in by_pfr.items():
        profile = known.get(pfr_id)
        if profile is None:
            skipped += 1
            continue
        _set_combine(profile, row)
        profile.updated_at = now
        updates.append(profile)
    if not dry_run and updates:
        PlayerProfile.objects.bulk_update(
            updates, PROFILE_COMBINE_UPDATE_FIELDS, batch_size=BATCH_SIZE
        )
    return len(updates), skipped


def sync_profiles(
    loader: ProfileSource | None = None,
    *,
    dry_run: bool = False,
    sources: tuple[str, ...] = ("ids", "combine"),
) -> SyncStats:
    """Enrich tracked players from the external releases.

    ``sources`` selects the passes: ``ids`` = draft capital + crosswalk ids
    (db_playerids, joined by ``sleeper_id``); ``combine`` = athleticism
    (nflverse, joined by ``pfr_id`` onto rows the draft pass matched). Both run
    inside one ``SyncRun`` so a single run records the combined tally. Idempotent:
    each pass upserts/updates in place.
    """
    loader = loader or DynastyProcessLoader()
    stats = SyncStats()

    with SyncRun.track(SyncRun.Kind.PROFILES) as run:
        if "ids" in sources:
            written, skipped = _sync_draft_capital(loader, dry_run=dry_run)
            stats.written += written
            stats.skipped += skipped
        if "combine" in sources:
            written, skipped = _sync_combine(loader, dry_run=dry_run)
            stats.written += written
            stats.skipped += skipped
        run.records_written = stats.written
        run.records_skipped = stats.skipped

    return stats
