import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

# sleeper_ids present in players_sample.json, by what they exercise.
CHASE = "7564"  # live WR, fully populated
TEXANS_DEF = "HOU"  # team defense: no full_name, no status, no age
LOVE = "13287"  # 2026 rookie on an NFL roster
BRADY = "167"  # retired, but active=True and team=None
LINEMAN = "6930"  # on a roster, but position C — not fantasy relevant
SENTINEL_RANK = "13600"  # on a roster, search_rank == 9999999


def load_players_fixture() -> dict[str, Any]:
    """The trimmed real Sleeper payload, keyed by player_id."""
    with (FIXTURE_DIR / "players_sample.json").open() as handle:
        return json.load(handle)


def load_stats_fixture() -> dict[str, Any]:
    """A trimmed weekly stats payload: two known ids plus one unknown."""
    with (FIXTURE_DIR / "player_stats_sample.json").open() as handle:
        return json.load(handle)


class FakeSleeperClient:
    """Stands in for SleeperClient. Records calls; never touches the network."""

    def __init__(
        self,
        players: dict[str, Any] | None = None,
        trending: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
        stats: dict[str, Any] | None = None,
        projections: dict[str, Any] | None = None,
        season: str = "2026",
    ) -> None:
        self._players = players if players is not None else load_players_fixture()
        self._trending = trending or []
        self._error = error
        self._stats = stats if stats is not None else load_stats_fixture()
        self._projections = (
            projections if projections is not None else load_stats_fixture()
        )
        self._season = season
        self.calls: list[str] = []

    def get_all_players(self) -> dict[str, Any]:
        self.calls.append("get_all_players")
        if self._error is not None:
            raise self._error
        return self._players

    def get_trending_players(
        self, kind: str = "add", lookback_hours: int = 24, limit: int = 25
    ) -> list[dict[str, Any]]:
        self.calls.append(f"get_trending_players:{kind}")
        if self._error is not None:
            raise self._error
        return self._trending

    def get_nfl_state(self) -> dict[str, Any]:
        self.calls.append("get_nfl_state")
        return {"season": self._season, "week": 0, "season_type": "off"}

    def get_player_stats(
        self, season: str, week: int, season_type: str = "regular"
    ) -> dict[str, Any]:
        self.calls.append(f"get_player_stats:{season}:{week}")
        if self._error is not None:
            raise self._error
        return self._stats

    def get_player_projections(
        self, season: str, week: int, season_type: str = "regular"
    ) -> dict[str, Any]:
        self.calls.append(f"get_player_projections:{season}:{week}")
        if self._error is not None:
            raise self._error
        return self._projections
