"""Builders for Sleeper league payloads, plus a fake client that serves them.

Shapes mirror the real API: rosters carry ``starters``/``players``/``taxi``/
``reserve`` lists of player-id strings, points are split into whole and
hundredths fields, and a dynasty's seasons are linked by ``previous_league_id``.
"""

from __future__ import annotations

from typing import Any

from apps.players.tests.utils import load_players_fixture
from apps.sleeper.client import SleeperAPIError

ME = "user_me"
RIVAL = "user_rival"


def make_user(
    user_id: str = ME,
    username: str = "dynastyguy",
    display_name: str = "Dynasty Guy",
    team_name: str = "",
) -> dict[str, Any]:
    user: dict[str, Any] = {
        "user_id": user_id,
        "username": username,
        "display_name": display_name,
        "avatar": "abc123",
    }
    if team_name:
        user["metadata"] = {"team_name": team_name}
    return user


def make_league(
    league_id: str,
    season: str,
    name: str = "The Dynasty",
    previous_league_id: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    league = {
        "league_id": league_id,
        "name": name,
        "season": season,
        "status": "complete",
        "total_rosters": 2,
        "previous_league_id": previous_league_id,
        "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"],
        "scoring_settings": {"rec": 1.0, "pass_td": 4.0},
        "settings": {"taxi_slots": 4, "num_teams": 2},
    }
    league.update(overrides)
    return league


def make_roster(
    roster_id: int,
    owner_id: str | None,
    starters: list[str] | None = None,
    players: list[str] | None = None,
    taxi: list[str] | None = None,
    reserve: list[str] | None = None,
    **settings: Any,
) -> dict[str, Any]:
    base = {
        "wins": 8,
        "losses": 5,
        "ties": 0,
        "fpts": 1450,
        "fpts_decimal": 55,
        "fpts_against": 1300,
        "fpts_against_decimal": 25,
        "waiver_budget_used": 40,
    }
    base.update(settings)
    return {
        "roster_id": roster_id,
        "owner_id": owner_id,
        "starters": starters or [],
        "players": players or [],
        "taxi": taxi or [],
        "reserve": reserve or [],
        "settings": base,
    }


class FakeLeagueClient:
    """Serves canned league data. Records calls; never touches the network."""

    def __init__(
        self,
        user: dict[str, Any] | None = None,
        user_leagues: list[dict[str, Any]] | None = None,
        leagues: dict[str, dict[str, Any]] | None = None,
        rosters: dict[str, list[dict[str, Any]]] | None = None,
        users: dict[str, list[dict[str, Any]]] | None = None,
        players: dict[str, Any] | None = None,
        season: str = "2026",
        user_error: Exception | None = None,
    ) -> None:
        self.user = user if user is not None else make_user()
        self.user_leagues = user_leagues or []
        self.leagues = leagues or {}
        self.rosters = rosters or {}
        self.users = users or {}
        self.players = players if players is not None else load_players_fixture()
        self.season = season
        self.user_error = user_error
        self.calls: list[str] = []

    def get_nfl_state(self) -> dict[str, Any]:
        self.calls.append("get_nfl_state")
        return {"season": self.season, "week": 0, "season_type": "off"}

    def get_all_players(self) -> dict[str, Any]:
        self.calls.append("get_all_players")
        return self.players

    def get_trending_players(
        self, kind: str = "add", lookback_hours: int = 24, limit: int = 25
    ) -> list[dict[str, Any]]:
        self.calls.append(f"get_trending_players:{kind}")
        return []

    def get_user(self, username_or_id: str) -> dict[str, Any]:
        self.calls.append(f"get_user:{username_or_id}")
        if self.user_error is not None:
            raise self.user_error
        return self.user

    def get_user_leagues(
        self, user_id: str, season: str, sport: str = "nfl"
    ) -> list[dict[str, Any]]:
        self.calls.append(f"get_user_leagues:{user_id}:{season}")
        return self.user_leagues

    def get_league(self, league_id: str) -> dict[str, Any] | None:
        self.calls.append(f"get_league:{league_id}")
        return self.leagues.get(league_id)

    def get_league_rosters(self, league_id: str) -> list[dict[str, Any]]:
        self.calls.append(f"get_league_rosters:{league_id}")
        return self.rosters.get(league_id, [])

    def get_league_users(self, league_id: str) -> list[dict[str, Any]]:
        self.calls.append(f"get_league_users:{league_id}")
        return self.users.get(league_id, [])


def unknown_user_client() -> FakeLeagueClient:
    return FakeLeagueClient(user_error=SleeperAPIError("No Sleeper user found"))
