from __future__ import annotations

from typing import Any

from django.db.models import Count, F, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import ListView

# The rookie board mirrors the 001 free-agent board; reuse its shared football
# ordering rather than re-deriving it. (leagues never imports scouting, so this
# one-way coupling is safe.)
from apps.leagues.views import PLAYER_POSITION_RANK
from apps.players.models import Player
from apps.scouting.models import ScoutingNote, Target

# Whitelisted sort keys → ORM paths on the annotated queryset. User input is
# never interpolated into order_by; anything unrecognised falls back to DEFAULT.
ROOKIE_SORTS: dict[str, str] = {
    "name": "full_name",
    "position": "position_rank",
    "age": "age",
    "rookie_year": "rookie_year",
    "college": "college",
}
ROOKIE_DEFAULT_SORT = "position"

# (sort key, column header). An empty key renders a non-sortable header. The
# first seven align with leagues/_player_row.html; the last is our controls cell.
ROOKIE_COLUMNS: list[tuple[str, str]] = [
    ("name", "Player"),
    ("position", "Pos"),
    ("", "NFL"),
    ("age", "Age"),
    ("", "Exp"),
    ("rookie_year", "Rookie"),
    ("", "Status"),
    ("", "Target"),
]

# The positions that matter for a rookie draft board — kept simple and explicit.
ROOKIE_POSITIONS = ["QB", "RB", "WR", "TE"]

# Hand-set tier options (1 = top tier).
ROOKIE_TIERS = range(1, 9)


def rookie_players(
    *,
    sort: str = ROOKIE_DEFAULT_SORT,
    direction: str = "asc",
    position: str = "",
    search: str = "",
    rookie_year: int | None = None,
) -> QuerySet[Player]:
    """The incoming rookie class — players with no NFL experience yet.

    Modelled on ``apps.leagues.views.free_agents``: a whitelisted sort resolved
    through ``F()`` (never raw user input), ``PLAYER_POSITION_RANK`` for football
    ordering, and ``select_related("target")`` + a ``note_count`` annotation so
    each row can overlay its ``Target`` and note count without an N+1.
    """
    players = Player.objects.filter(years_exp=0)
    if rookie_year is not None:
        players = players.filter(rookie_year=rookie_year)
    if position:
        players = players.filter(position=position)
    if search:
        players = players.filter(full_name__icontains=search)

    players = players.select_related("target").annotate(
        position_rank=PLAYER_POSITION_RANK,
        note_count=Count("scouting_notes"),
    )

    field = F(ROOKIE_SORTS.get(sort, ROOKIE_SORTS[ROOKIE_DEFAULT_SORT]))
    ordering = (
        field.desc(nulls_last=True)
        if direction == "desc"
        else field.asc(nulls_last=True)
    )
    return players.order_by(ordering, "full_name")


def _row_queryset() -> QuerySet[Player]:
    """One player with the same overlay the table row template expects."""
    return Player.objects.select_related("target").annotate(
        note_count=Count("scouting_notes")
    )


def _render_row(request: HttpRequest, pk: int) -> HttpResponse:
    player = get_object_or_404(_row_queryset(), pk=pk)
    return render(request, "scouting/_rookie_row.html", _row_context(player))


def _row_context(player: Player) -> dict[str, Any]:
    return {
        "player": player,
        "stances": Target.Stance.choices,
        "priorities": Target.Priority.choices,
        "tiers": ROOKIE_TIERS,
    }


class RookieBoardView(ListView):
    """The upcoming rookie class — filterable, sortable, paginated draft board."""

    template_name = "scouting/rookie_board.html"
    context_object_name = "players"
    paginate_by = 50

    def filter_params(self) -> dict[str, Any]:
        sort = self.request.GET.get("sort", ROOKIE_DEFAULT_SORT)
        if sort not in ROOKIE_SORTS:
            sort = ROOKIE_DEFAULT_SORT
        raw_year = self.request.GET.get("rookie_year", "")
        try:
            rookie_year = int(raw_year) if raw_year else None
        except ValueError:
            rookie_year = None
        return {
            "sort": sort,
            "dir": "desc" if self.request.GET.get("dir") == "desc" else "asc",
            "pos": self.request.GET.get("pos", ""),
            "q": self.request.GET.get("q", "").strip(),
            "rookie_year": rookie_year,
        }

    def get_queryset(self) -> QuerySet[Player]:
        self.params = self.filter_params()
        return rookie_players(
            sort=self.params["sort"],
            direction=self.params["dir"],
            position=self.params["pos"],
            search=self.params["q"],
            rookie_year=self.params["rookie_year"],
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(self.params)
        context["columns"] = ROOKIE_COLUMNS
        context["positions"] = ROOKIE_POSITIONS
        context["stances"] = Target.Stance.choices
        context["priorities"] = Target.Priority.choices
        context["tiers"] = ROOKIE_TIERS
        context["querystring"] = self.querystring()
        return context

    def querystring(self) -> str:
        """Current filters, minus sort/dir/page — for building sort links."""
        parts = []
        if self.params["pos"]:
            parts.append(f"pos={self.params['pos']}")
        if self.params["q"]:
            parts.append(f"q={self.params['q']}")
        if self.params["rookie_year"] is not None:
            parts.append(f"rookie_year={self.params['rookie_year']}")
        return "&".join(parts)


class RookieTableView(RookieBoardView):
    """HTMX endpoint returning just the rookie table fragment."""

    template_name = "scouting/_rookie_table.html"


@require_POST
def set_target(request: HttpRequest, pk: int) -> HttpResponse:
    """Create/update or clear the player's Target, then re-render its row.

    A blank or unrecognised stance clears the target (delete); the ``OneToOne``
    means setting one is an ``update_or_create``.
    """
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
            defaults={"stance": stance, "tier": tier, "priority": priority},
        )
    else:
        Target.objects.filter(player=player).delete()
    return _render_row(request, pk)


@require_POST
def add_note(request: HttpRequest, pk: int) -> HttpResponse:
    """Append a scouting note (blank ignored), then re-render the player's row."""
    player = get_object_or_404(Player, pk=pk)
    body = request.POST.get("body", "").strip()
    if body:
        ScoutingNote.objects.create(player=player, body=body)
    return _render_row(request, pk)
