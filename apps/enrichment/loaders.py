"""File loaders for external enrichment releases.

The source is a **static CSV release over HTTP** (DynastyProcess `db_playerids`),
not the Sleeper API and not web scraping — so it gets its own thin loader rather
than routing through ``SleeperClient``. Same discipline: a retrying session, a
bounded timeout, a narrow capability ``Protocol`` so the service depends on an
interface (and tests pass a fake), and one error type.
"""

from __future__ import annotations

import csv
import io
from typing import Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# DynastyProcess publishes db_playerids.csv (draft capital + a sleeper_id
# crosswalk) as a versioned, auth-free file. Confirm the current release URL;
# override per-run with `sync_profiles --url`.
DB_PLAYERIDS_URL = (
    "https://raw.githubusercontent.com/dynastyprocess/data/master/files/"
    "db_playerids.csv"
)

# nflverse combine release (athleticism measurables, keyed by pfr_id). Confirm
# the current URL; override per-run with `sync_profiles --combine-url`.
NFLVERSE_COMBINE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/combine/combine.csv"
)

DEFAULT_TIMEOUT = (5.0, 60.0)
USER_AGENT = "dynasty-hq/0.1 (+personal fantasy football tooling)"


class ProfileLoadError(RuntimeError):
    """A release download failed, returned a non-200, or wasn't parseable CSV."""


class ProfileSource(Protocol):
    """What ``sync_profiles`` needs — the releases parsed into raw dict rows."""

    def fetch_player_ids(self) -> list[dict[str, str]]: ...

    def fetch_combine(self) -> list[dict[str, str]]: ...


class DynastyProcessLoader:
    """Downloads ``db_playerids.csv`` and returns it as a list of dict rows."""

    def __init__(
        self,
        url: str = DB_PLAYERIDS_URL,
        combine_url: str = NFLVERSE_COMBINE_URL,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = url
        self.combine_url = combine_url
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

    def fetch_player_ids(self) -> list[dict[str, str]]:
        text = self._download(self.url)
        return list(csv.DictReader(io.StringIO(text)))

    def fetch_combine(self) -> list[dict[str, str]]:
        text = self._download(self.combine_url)
        return list(csv.DictReader(io.StringIO(text)))

    def _download(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProfileLoadError(f"GET {url} failed: {exc}") from exc
        if response.status_code != 200:
            raise ProfileLoadError(f"GET {url} returned HTTP {response.status_code}")
        return response.text
