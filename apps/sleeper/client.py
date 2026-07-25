"""Thin HTTP client for the public Sleeper API.

The API is read-only and needs no authentication. Sleeper asks callers to stay
under 1000 requests/minute, and to fetch the full player dump at most once a day
(it is ~15 MB) — the throttle for that lives in the sync service, not here.
"""

from __future__ import annotations

from typing import Any, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://api.sleeper.app/v1"

# (connect, read). The read timeout is generous because /players/nfl is ~15 MB.
DEFAULT_TIMEOUT = (5.0, 120.0)

USER_AGENT = "dynasty-hq/0.1 (+personal fantasy football tooling)"


class SleeperAPIError(RuntimeError):
    """A Sleeper request failed, returned a non-200, or returned invalid JSON."""


"""Protocols below are split by capability rather than exposing one fat
interface: each sync service asks for only the calls it makes, so a test fake
only has to implement that much. ``SleeperClient`` satisfies all of them."""


class PlayerSource(Protocol):
    """What ``sync_players`` needs."""

    def get_all_players(self) -> dict[str, Any]: ...


class TrendingSource(Protocol):
    """What the trending sync needs."""

    def get_trending_players(
        self, kind: str = ..., lookback_hours: int = ..., limit: int = ...
    ) -> list[dict[str, Any]]: ...


class LeagueSource(PlayerSource, Protocol):
    """What ``sync_leagues`` needs.

    Inherits ``get_all_players`` because the roster sync backfills any player
    the live-player filter excluded.
    """

    def get_nfl_state(self) -> dict[str, Any]: ...

    def get_user(self, username_or_id: str) -> dict[str, Any]: ...

    def get_user_leagues(
        self, user_id: str, season: str, sport: str = ...
    ) -> list[dict[str, Any]]: ...

    def get_league(self, league_id: str) -> dict[str, Any] | None: ...

    def get_league_rosters(self, league_id: str) -> list[dict[str, Any]]: ...

    def get_league_users(self, league_id: str) -> list[dict[str, Any]]: ...


class TransactionSource(Protocol):
    """What ``sync_transactions`` needs."""

    def get_league_transactions(
        self, league_id: str, week: int
    ) -> list[dict[str, Any]]: ...

    def get_traded_picks(self, league_id: str) -> list[dict[str, Any]]: ...


class StatsSource(Protocol):
    """What ``sync_stats`` needs."""

    def get_nfl_state(self) -> dict[str, Any]: ...

    def get_player_stats(
        self, season: str, week: int, season_type: str = ...
    ) -> dict[str, Any]: ...

    def get_player_projections(
        self, season: str, week: int, season_type: str = ...
    ) -> dict[str, Any]: ...


class SleeperAPI(
    LeagueSource, TrendingSource, TransactionSource, StatsSource, Protocol
):
    """The full API surface — everything ``SleeperClient`` provides."""


class SleeperClient:
    """Wraps the Sleeper REST API.

    Retries transient failures (429 and 5xx) with exponential backoff. Any
    non-200 that survives the retries is raised as ``SleeperAPIError`` so that
    callers never have to inspect status codes.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session if session is not None else self.build_session()

    @staticmethod
    def build_session() -> requests.Session:
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": USER_AGENT})
        return session

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> Any:
        """GET and decode JSON.

        ``missing_ok`` turns a 404 into ``None`` instead of an error. Sleeper is
        inconsistent about absent resources — an unknown *user* comes back as
        ``200 null``, an unknown *league* as ``404``, and an unknown league's
        ``/users`` as ``200 []`` — so the caller decides which is which.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SleeperAPIError(f"GET {url} failed: {exc}") from exc

        if missing_ok and response.status_code == 404:
            return None

        if response.status_code != 200:
            raise SleeperAPIError(f"GET {url} returned HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise SleeperAPIError(f"GET {url} returned invalid JSON") from exc

    # --- endpoints -------------------------------------------------------

    def get_nfl_state(self) -> dict[str, Any]:
        """Current season, week, and season type."""
        return self._get("state/nfl")

    def get_all_players(self) -> dict[str, Any]:
        """Every player Sleeper knows about, keyed by ``player_id``.

        ~15 MB and ~12,200 records. Call at most once a day.
        """
        return self._get("players/nfl")

    def get_trending_players(
        self,
        kind: str = "add",
        lookback_hours: int = 24,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Most-added or most-dropped players across all of Sleeper.

        Returns ``[{"player_id": ..., "count": ...}]`` — no player detail.
        """
        if kind not in {"add", "drop"}:
            raise ValueError(f"kind must be 'add' or 'drop', got {kind!r}")
        return self._get(
            f"players/nfl/trending/{kind}",
            params={"lookback_hours": lookback_hours, "limit": limit},
        )

    def get_user(self, username_or_id: str) -> dict[str, Any]:
        """Resolve a username (or user_id) to a user object.

        Sleeper answers 200 with a ``null`` body for an unknown username, so
        that case is turned into an error here rather than a silent ``None``.
        """
        user = self._get(f"user/{username_or_id}")
        if not user:
            raise SleeperAPIError(f"No Sleeper user found for {username_or_id!r}")
        return user

    def get_user_leagues(
        self, user_id: str, season: str, sport: str = "nfl"
    ) -> list[dict[str, Any]]:
        return self._get(f"user/{user_id}/leagues/{sport}/{season}") or []

    def get_league(self, league_id: str) -> dict[str, Any] | None:
        """One league-season. ``None`` if Sleeper doesn't know it.

        Returning rather than raising matters: a dynasty's ``previous_league_id``
        can point at a season Sleeper has since purged, and the chain walk must
        stop cleanly rather than blow up the whole sync.
        """
        return self._get(f"league/{league_id}", missing_ok=True) or None

    def get_league_rosters(self, league_id: str) -> list[dict[str, Any]]:
        return self._get(f"league/{league_id}/rosters", missing_ok=True) or []

    def get_league_users(self, league_id: str) -> list[dict[str, Any]]:
        return self._get(f"league/{league_id}/users") or []

    def get_league_transactions(
        self, league_id: str, week: int
    ) -> list[dict[str, Any]]:
        """All transactions for one week — trades, waivers, and FA moves.

        Per-week; callers filter to the types they want. A purged/unknown league
        404s → ``[]``, the same swallow ``get_league_rosters`` uses.
        """
        return (
            self._get(f"league/{league_id}/transactions/{week}", missing_ok=True) or []
        )

    def get_traded_picks(self, league_id: str) -> list[dict[str, Any]]:
        """Draft picks that have changed hands — the current-ownership snapshot."""
        return self._get(f"league/{league_id}/traded_picks", missing_ok=True) or []

    def get_player_stats(
        self, season: str, week: int, season_type: str = "regular"
    ) -> dict[str, Any]:
        """Every player's actual stat line for one NFL week, keyed by player_id.

        A season/week Sleeper has no data for returns ``null`` → ``{}`` here.
        """
        return self._get(f"stats/nfl/{season_type}/{season}/{week}") or {}

    def get_player_projections(
        self, season: str, week: int, season_type: str = "regular"
    ) -> dict[str, Any]:
        """Every player's projected stat line for one NFL week, keyed by player_id."""
        return self._get(f"projections/nfl/{season_type}/{season}/{week}") or {}
