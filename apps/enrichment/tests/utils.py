import csv
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def load_playerids_fixture() -> list[dict[str, str]]:
    """The trimmed db_playerids release: two matchable ids + two skips."""
    with (FIXTURE_DIR / "db_playerids_sample.csv").open() as handle:
        return list(csv.DictReader(handle))


def load_combine_fixture() -> list[dict[str, str]]:
    """The trimmed nflverse combine release, keyed by pfr_id."""
    with (FIXTURE_DIR / "combine_sample.csv").open() as handle:
        return list(csv.DictReader(handle))


class FakeProfileLoader:
    """Stands in for DynastyProcessLoader. Records calls; never touches network."""

    def __init__(
        self,
        rows: list[dict[str, str]] | None = None,
        error: Exception | None = None,
        combine: list[dict[str, str]] | None = None,
    ) -> None:
        self._rows = rows if rows is not None else load_playerids_fixture()
        self._combine = combine if combine is not None else load_combine_fixture()
        self._error = error
        self.calls: list[str] = []

    def fetch_player_ids(self) -> list[dict[str, str]]:
        self.calls.append("fetch_player_ids")
        if self._error is not None:
            raise self._error
        return self._rows

    def fetch_combine(self) -> list[dict[str, str]]:
        self.calls.append("fetch_combine")
        if self._error is not None:
            raise self._error
        return self._combine
