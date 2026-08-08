from __future__ import annotations

import pytest

from app.market_data.cache import PriceCache


@pytest.fixture
def price_cache() -> PriceCache:
    return PriceCache()


class FakeTrackedSet:
    """A hand-rolled `TrackedSetProvider` stand-in for driver tests — avoids
    needing a real SQLite database to exercise `run_market_data_cycle`."""

    def __init__(self, tickers=()) -> None:
        self._tickers = set(tickers)

    def set(self, tickers) -> None:
        self._tickers = set(tickers)

    async def get(self) -> set[str]:
        return set(self._tickers)


@pytest.fixture
def tracked_set_stub() -> FakeTrackedSet:
    return FakeTrackedSet()
