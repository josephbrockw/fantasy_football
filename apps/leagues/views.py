from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import (
    Case,
    Count,
    F,
    IntegerField,
    OuterRef,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, ListView, TemplateView

from apps.leagues.models import League, LeagueSeason, RosterSlot, Team
from apps.leagues.services import starting_slots
from apps.players.models import Player, TrendingPlayer
from apps.sleeper.models import SyncRun

# Whitelisted sort keys → ORM paths. User input is never interpolated into
# order_by; anything unrecognised falls back to DEFAULT_SORT.
SORT_FIELDS: dict[str, str] = {
    "position": "",  # handled by POSITION_RANK below, not a plain field
    "name": "player__full_name",
    "team": "player__team",
    "age": "player__age",
    "experience": "player__years_exp",
    "rookie_year": "player__rookie_year",
}
DEFAULT_SORT = "position"

# (sort key, column header). An empty key renders a non-sortable header.
SORTABLE_COLUMNS: list[tuple[str, str]] = [
    ("name", "Player"),
    ("position", "Pos"),
    ("team", "NFL"),
    ("age", "Age"),
    ("experience", "Exp"),
    ("rookie_year", "Rookie"),
    ("", "Status"),
]

# Football order, not alphabetical — QB, RB, WR, TE reads correctly to anyone
# who plays fantasy; "QB, RB, TE, WR" does not.
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]

POSITION_RANK = Case(
    *[
        When(player__position=position, then=Value(index))
        for index, position in enumerate(POSITION_ORDER)
    ],
    default=Value(len(POSITION_ORDER)),
    output_field=IntegerField(),
)

# Reserve groups, in the order they're rendered. Starters are handled
# separately because their order is the league's lineup, not a sort.
RESERVE_SLOTS = [RosterSlot.Slot.BENCH, RosterSlot.Slot.TAXI, RosterSlot.Slot.IR]


@dataclass
class LineupRow:
    """One slot in the league's declared starting lineup."""

    index: int
    position: str
    slot: RosterSlot | None

    @property
    def is_empty(self) -> bool:
        return self.slot is None

    @property
    def is_flex(self) -> bool:
        return "FLEX" in self.position


def reserve_slots(
    team: Team, sort: str, direction: str, position: str
) -> QuerySet[RosterSlot]:
    """Non-starting roster slots, sorted and optionally filtered."""
    slots = (
        RosterSlot.objects.filter(team=team, slot__in=RESERVE_SLOTS)
        .select_related("player")
        .annotate(position_rank=POSITION_RANK)
    )
    if position:
        slots = slots.filter(player__position=position)

    field = F("position_rank") if sort == "position" else F(SORT_FIELDS[sort])
    # NULLs last either way — an unknown age shouldn't head an age sort.
    ordering = (
        field.desc(nulls_last=True)
        if direction == "desc"
        else field.asc(nulls_last=True)
    )
    return slots.order_by(ordering, "player__full_name")


def group_reserves(
    slots: QuerySet[RosterSlot] | list[RosterSlot],
) -> list[tuple[str, str, list[RosterSlot]]]:
    """Bucket reserves into (value, label, rows), preserving RESERVE_SLOTS order."""
    labels = dict(RosterSlot.Slot.choices)
    buckets: dict[str, list[RosterSlot]] = {slot: [] for slot in RESERVE_SLOTS}
    for slot in slots:
        buckets.setdefault(slot.slot, []).append(slot)
    return [
        (value, labels.get(value, value), buckets[value])
        for value in RESERVE_SLOTS
        if buckets[value]
    ]


def starting_lineup(team: Team) -> list[LineupRow]:
    """The league's starting slots in declared order, with who fills each.

    Driven by the league's ``roster_positions`` rather than by whoever happens
    to be flagged a starter, so an unfilled slot shows up as a gap instead of
    silently vanishing.
    """
    starters = list(
        RosterSlot.objects.filter(
            team=team, slot=RosterSlot.Slot.STARTER
        ).select_related("player")
    )
    by_index = {s.lineup_order: s for s in starters if s.lineup_order is not None}

    lineup = [
        LineupRow(index=index, position=position, slot=by_index.get(index))
        for index, position in enumerate(
            starting_slots(team.league_season.roster_positions)
        )
    ]

    # Defensive: a starter the declared lineup doesn't account for — synced
    # before alignment was tracked, or an index past the league's slot list —
    # would otherwise disappear from the page entirely.
    covered = set(range(len(lineup)))
    orphans = sorted(
        (s for s in starters if s.lineup_order not in covered),
        key=lambda s: (s.lineup_order is None, s.lineup_order or 0),
    )
    lineup.extend(
        LineupRow(index=len(lineup) + offset, position="", slot=slot)
        for offset, slot in enumerate(orphans)
    )
    return lineup


class RosterFilterMixin:
    """Shared parsing of the ?sort / ?dir / ?pos query params."""

    request: Any

    def filter_params(self) -> dict[str, str]:
        sort = self.request.GET.get("sort", DEFAULT_SORT)
        if sort not in SORT_FIELDS:
            sort = DEFAULT_SORT
        direction = "desc" if self.request.GET.get("dir") == "desc" else "asc"
        return {
            "sort": sort,
            "dir": direction,
            "pos": self.request.GET.get("pos", ""),
        }

    def reserve_context(self, team: Team) -> dict[str, Any]:
        params = self.filter_params()
        return {
            **params,
            "team": team,
            "sortable_columns": SORTABLE_COLUMNS,
            "reserve_groups": group_reserves(
                reserve_slots(team, params["sort"], params["dir"], params["pos"])
            ),
        }


class DashboardView(TemplateView):
    """Landing page: my current team in each league, plus sync freshness."""

    template_name = "leagues/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)

        # Newest season first within each league, so the first row per league
        # is the current one. One card per league, not one per season.
        teams = (
            Team.objects.filter(manager__is_me=True)
            .select_related("manager", "league_season", "league_season__league")
            .annotate(player_count=Count("roster_slots"))
            .order_by("league_season__league__name", "-league_season__season")
        )
        seen: set[int] = set()
        my_teams = []
        for team in teams:
            league_id = team.league_season.league_id
            if league_id not in seen:
                seen.add(league_id)
                my_teams.append(team)

        context["my_teams"] = my_teams
        context["leagues"] = League.objects.all()
        context["has_data"] = bool(my_teams) or League.objects.exists()
        context["sync_runs"] = [
            (label, SyncRun.last_success(kind)) for kind, label in SyncRun.Kind.choices
        ]
        return context


class LeagueOverviewView(DetailView):
    """Standings for one season of a league."""

    model = League
    slug_field = "slug"
    template_name = "leagues/league_overview.html"
    context_object_name = "league"

    def pick_season(self, seasons: list[LeagueSeason]) -> LeagueSeason | None:
        """The requested season, else the newest. ``seasons`` is newest-first."""
        requested = self.request.GET.get("season")
        if requested:
            return next((s for s in seasons if s.season == requested), None)
        return seasons[0] if seasons else None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        league: League = self.object
        # One query for the picker, reused to resolve the selected season.
        seasons = list(league.seasons.all())
        season = self.pick_season(seasons)

        context["season"] = season
        context["seasons"] = seasons
        context["teams"] = (
            Team.objects.filter(league_season=season)
            .select_related("manager")
            .annotate(player_count=Count("roster_slots"))
            # Explicit: Django discards Meta.ordering on aggregate queries, so
            # annotate() would otherwise leave the standings unsorted.
            .order_by("-wins", "-points_for")
            if season
            else Team.objects.none()
        )
        return context


class TeamDetailView(RosterFilterMixin, DetailView):
    """One team: the starting lineup in league order, then the reserves."""

    model = Team
    template_name = "leagues/team_detail.html"
    context_object_name = "team"

    def get_queryset(self) -> QuerySet[Team]:
        return Team.objects.select_related(
            "manager", "league_season", "league_season__league"
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(self.reserve_context(self.object))
        context["lineup"] = starting_lineup(self.object)
        context["positions"] = sorted(
            RosterSlot.objects.filter(team=self.object, slot__in=RESERVE_SLOTS)
            .exclude(player__position="")
            # order_by() clears Meta.ordering. Without it `slot` joins the
            # SELECT and DISTINCT dedupes (position, slot) pairs, so a position
            # appearing in two slots would render a duplicate filter chip.
            .order_by()
            .values_list("player__position", flat=True)
            .distinct(),
            key=lambda p: (
                POSITION_ORDER.index(p) if p in POSITION_ORDER else len(POSITION_ORDER),
                p,
            ),
        )
        return context


class TeamReserveTableView(RosterFilterMixin, TemplateView):
    """HTMX endpoint returning just the reserves fragment."""

    template_name = "leagues/_reserve_tables.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        team = get_object_or_404(Team, pk=kwargs["pk"])
        context.update(self.reserve_context(team))
        return context


# --- Free agents -------------------------------------------------------


FREE_AGENT_SORTS: dict[str, str] = {
    "trending": "trend_add",
    "position": "position_rank",
    "name": "full_name",
    "team": "team",
    "age": "age",
    "experience": "years_exp",
    "rookie_year": "rookie_year",
}
FREE_AGENT_DEFAULT_SORT = "trending"

FREE_AGENT_COLUMNS: list[tuple[str, str]] = [
    ("name", "Player"),
    ("position", "Pos"),
    ("team", "NFL"),
    ("age", "Age"),
    ("experience", "Exp"),
    ("rookie_year", "Rookie"),
    ("", "Status"),
    ("trending", "Adds 24h"),
]

PLAYER_POSITION_RANK = Case(
    *[
        When(position=position, then=Value(index))
        for index, position in enumerate(POSITION_ORDER)
    ],
    default=Value(len(POSITION_ORDER)),
    output_field=IntegerField(),
)


def free_agents(
    season: LeagueSeason,
    *,
    sort: str = FREE_AGENT_DEFAULT_SORT,
    direction: str = "desc",
    position: str = "",
    max_age: int | None = None,
    search: str = "",
    include_inactive: bool = False,
) -> QuerySet[Player]:
    """Players not rostered anywhere in this season of this league.

    Derived, never stored — being a free agent is the absence of a roster spot,
    so materialising it would just be a cache to invalidate.
    """
    rostered = RosterSlot.objects.filter(team__league_season=season).values("player_id")

    players = Player.objects.exclude(pk__in=rostered)

    # Only positions this league actually rosters — no kickers in a league
    # that doesn't start one.
    league_positions = season.fantasy_positions
    if league_positions:
        players = players.filter(position__in=league_positions)
    if position:
        players = players.filter(position=position)
    if max_age is not None:
        players = players.filter(age__lte=max_age)
    if search:
        players = players.filter(full_name__icontains=search)
    if not include_inactive:
        players = players.exclude(status="Inactive")

    players = players.annotate(
        position_rank=PLAYER_POSITION_RANK,
        trend_add=Coalesce(
            Subquery(
                TrendingPlayer.objects.filter(
                    player=OuterRef("pk"), kind=TrendingPlayer.Kind.ADD
                ).values("count")[:1]
            ),
            Value(0),
        ),
        trend_drop=Coalesce(
            Subquery(
                TrendingPlayer.objects.filter(
                    player=OuterRef("pk"), kind=TrendingPlayer.Kind.DROP
                ).values("count")[:1]
            ),
            Value(0),
        ),
    )

    field = F(FREE_AGENT_SORTS.get(sort, FREE_AGENT_SORTS[FREE_AGENT_DEFAULT_SORT]))
    ordering = (
        field.desc(nulls_last=True)
        if direction == "desc"
        else field.asc(nulls_last=True)
    )
    # search_rank is only ever a tiebreak: it collides heavily and is not an
    # ADP. Never surface it as a rank or sort by it alone.
    return players.order_by(
        ordering, F("search_rank").asc(nulls_last=True), "full_name"
    )


class FreeAgentListView(ListView):
    """Unrostered players for a league's current season."""

    template_name = "leagues/free_agents.html"
    context_object_name = "players"
    paginate_by = 50

    def get_league(self) -> League:
        return get_object_or_404(League, slug=self.kwargs["slug"])

    def filter_params(self) -> dict[str, Any]:
        sort = self.request.GET.get("sort", FREE_AGENT_DEFAULT_SORT)
        if sort not in FREE_AGENT_SORTS:
            sort = FREE_AGENT_DEFAULT_SORT
        raw_age = self.request.GET.get("max_age", "")
        try:
            max_age = int(raw_age) if raw_age else None
        except ValueError:
            max_age = None
        return {
            "sort": sort,
            "dir": "asc" if self.request.GET.get("dir") == "asc" else "desc",
            "pos": self.request.GET.get("pos", ""),
            "max_age": max_age,
            "q": self.request.GET.get("q", "").strip(),
            "include_inactive": self.request.GET.get("inactive") == "1",
        }

    def get_queryset(self) -> QuerySet[Player]:
        self.league = self.get_league()
        self.season = self.league.current_season
        self.params = self.filter_params()
        if self.season is None:
            return Player.objects.none()
        return free_agents(
            self.season,
            sort=self.params["sort"],
            direction=self.params["dir"],
            position=self.params["pos"],
            max_age=self.params["max_age"],
            search=self.params["q"],
            include_inactive=self.params["include_inactive"],
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(self.params)
        context["league"] = self.league
        context["season"] = self.season
        context["positions"] = self.season.fantasy_positions if self.season else []
        context["columns"] = FREE_AGENT_COLUMNS
        context["querystring"] = self.querystring()
        return context

    def querystring(self) -> str:
        """Current filters, minus sort/dir/page — for building sort links."""
        parts = []
        if self.params["pos"]:
            parts.append(f"pos={self.params['pos']}")
        if self.params["max_age"] is not None:
            parts.append(f"max_age={self.params['max_age']}")
        if self.params["q"]:
            parts.append(f"q={self.params['q']}")
        if self.params["include_inactive"]:
            parts.append("inactive=1")
        return "&".join(parts)


class FreeAgentTableView(FreeAgentListView):
    """HTMX endpoint returning just the free agent table."""

    template_name = "leagues/_free_agent_table.html"
