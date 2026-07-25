from __future__ import annotations

from itertools import groupby
from typing import Any

from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.leagues.models import League, RosterSlot

# The boards mirror the 001 free-agent board; reuse its shared football ordering
# rather than re-deriving it. (leagues never imports scouting, so this one-way
# coupling is safe.)
from apps.leagues.views import PLAYER_POSITION_RANK
from apps.players.models import Player
from apps.scouting.models import ScoutingNote, Target

# Positions that matter for a rookie draft board.
ROOKIE_POSITIONS = ["QB", "RB", "WR", "TE"]
# Hand-set tier options (1 = top tier).
ROOKIE_TIERS = range(1, 9)


def _with_target_overlay(players: QuerySet[Player], league: League) -> QuerySet[Player]:
    """Annotate each player with *this league's* target + note count.

    Correlated subqueries, so the overlay costs no extra query per row and the
    same row template works on every board. ``player.target`` would be wrong
    here — a player can be targeted in one league and not another.
    """
    target = Target.objects.filter(player=OuterRef("pk"), league=league)
    return players.annotate(
        target_stance=Subquery(target.values("stance")[:1]),
        target_tier=Subquery(target.values("tier")[:1]),
        target_priority=Subquery(target.values("priority")[:1]),
        note_count=Count("scouting_notes", filter=Q(scouting_notes__league=league)),
    )


def _control_context() -> dict[str, Any]:
    return {
        "stances": Target.Stance.choices,
        "priorities": Target.Priority.choices,
        "tiers": ROOKIE_TIERS,
    }


def rookie_players(
    league: League, *, position: str = "", search: str = ""
) -> QuerySet[Player]:
    """The incoming rookie class (no NFL experience), overlaid with league targets.

    Ordered by football position then tier (nulls last) then name, so the board
    reads as a draft board once grouped by position.
    """
    players = Player.objects.filter(years_exp=0)
    if position:
        players = players.filter(position=position)
    if search:
        players = players.filter(full_name__icontains=search)
    players = _with_target_overlay(players, league).annotate(
        position_rank=PLAYER_POSITION_RANK
    )
    # Within a position: my hand-set tier wins, then Sleeper's coarse search
    # ordering as a "notable first" proxy (search_rank is NOT an ADP — only ever
    # a tiebreak, never displayed), then name. A real valuation is the ML feature.
    return players.order_by(
        "position_rank",
        F("target_tier").asc(nulls_last=True),
        F("search_rank").asc(nulls_last=True),
        "full_name",
    )


def targeted_players(league: League, *, stance: str = "") -> QuerySet[Player]:
    """Players I've marked in this league, overlaid with their roster location.

    Geared toward my team: each row shows which team currently rosters the
    player (and whether that team is mine), so the board reads as a shortlist of
    who to chase or steer clear of.
    """
    players = _with_target_overlay(Player.objects.all(), league).filter(
        targets__league=league
    )
    if stance:
        players = players.filter(targets__stance=stance)

    season = league.current_season
    if season is not None:
        slot = RosterSlot.objects.filter(
            team__league_season=season, player=OuterRef("pk")
        )
        players = players.annotate(
            rostered_team_id=Subquery(slot.values("team_id")[:1]),
            rostered_team=Subquery(slot.values("team__team_name")[:1]),
            rostered_manager=Subquery(slot.values("team__manager__display_name")[:1]),
            rostered_by_me=Subquery(slot.values("team__manager__is_me")[:1]),
        )
    return players.order_by(
        F("target_stance"), F("target_tier").asc(nulls_last=True), "full_name"
    )


def _group(players: QuerySet[Player], key: str) -> list[dict[str, Any]]:
    materialised = list(players)
    groups = []
    for value, items in groupby(materialised, key=lambda p: getattr(p, key)):
        groups.append({"key": value, "players": list(items)})
    return groups


class RookieBoardView(TemplateView):
    """Per-league rookie draft board, grouped by position."""

    template_name = "scouting/rookie_board.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        league = get_object_or_404(League, slug=self.kwargs["slug"])
        pos = self.request.GET.get("pos", "")
        q = self.request.GET.get("q", "").strip()
        players = rookie_players(league, position=pos, search=q)
        groups = _group(players, "position")
        context.update(
            league=league,
            groups=groups,
            count=sum(len(g["players"]) for g in groups),
            pos=pos,
            q=q,
            positions=ROOKIE_POSITIONS,
            **_control_context(),
        )
        return context


class RookieTableView(RookieBoardView):
    """HTMX endpoint returning just the grouped rookie table."""

    template_name = "scouting/_rookie_table.html"


class TargetBoardView(TemplateView):
    """Per-league targets shortlist, grouped by stance (acquire / avoid)."""

    template_name = "scouting/targets_board.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        league = get_object_or_404(League, slug=self.kwargs["slug"])
        stance = self.request.GET.get("stance", "")
        if stance not in Target.Stance.values:
            stance = ""
        players = targeted_players(league, stance=stance)
        groups = _group(players, "target_stance")
        context.update(
            league=league,
            groups=groups,
            count=sum(len(g["players"]) for g in groups),
            stance=stance,
            stance_filters=Target.Stance.choices,
            **_control_context(),
        )
        return context


class TargetTableView(TargetBoardView):
    """HTMX endpoint returning just the grouped targets table."""

    template_name = "scouting/_targets_table.html"


def _annotated_player(league: League, pk: int) -> Player:
    return get_object_or_404(_with_target_overlay(Player.objects.all(), league), pk=pk)


def _render_controls(
    request: HttpRequest, league: League, player: Player, *, is_open: bool = False
) -> HttpResponse:
    context = {
        "league": league,
        "player": player,
        "controls_open": is_open,
        **_control_context(),
    }
    return render(request, "scouting/_target_controls.html", context)


def target_widget(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Lazy-load the inline control for one player — reusable on any screen."""
    league = get_object_or_404(League, slug=slug)
    return _render_controls(request, league, _annotated_player(league, pk))


@require_POST
def set_target(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Create/update or clear the player's target in this league; re-render it.

    A blank or unrecognised stance clears the target; ``unique(player, league)``
    means setting one is an ``update_or_create``.
    """
    league = get_object_or_404(League, slug=slug)
    player = get_object_or_404(Player, pk=pk)
    stance = request.POST.get("stance", "").strip()
    if stance in Target.Stance.values:
        raw_tier = request.POST.get("tier", "").strip()
        try:
            tier = int(raw_tier) if raw_tier else None
        except ValueError:
            tier = None
        priority = request.POST.get("priority", "").strip()
        if priority not in Target.Priority.values:
            priority = Target.Priority.MEDIUM
        Target.objects.update_or_create(
            player=player,
            league=league,
            defaults={"stance": stance, "tier": tier, "priority": priority},
        )
    else:
        Target.objects.filter(player=player, league=league).delete()
    # Keep the editor open so tier/priority can be set right after the stance.
    return _render_controls(
        request, league, _annotated_player(league, pk), is_open=True
    )


@require_POST
def add_note(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Append a scouting note in this league (blank ignored); re-render the control."""
    league = get_object_or_404(League, slug=slug)
    player = get_object_or_404(Player, pk=pk)
    body = request.POST.get("body", "").strip()
    if body:
        ScoutingNote.objects.create(player=player, league=league, body=body)
    return _render_controls(
        request, league, _annotated_player(league, pk), is_open=True
    )
