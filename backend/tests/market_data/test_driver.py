from __future__ import annotations

import asyncio
from typing import AbstractSet, Mapping

import pytest

from app.market_data.cache import PriceCache
from app.market_data.driver import run_market_data_cycle, run_market_data_loop
from app.market_data.models import ReferenceKind, TickerStatus
from app.market_data.provider import MarketDataProvider, Quote


class FakeProvider(MarketDataProvider):
    """A hand-rolled provider for testing the driver without real GBM math
    or a real HTTP call."""

    poll_interval_seconds = 0.0

    def __init__(self, answers: dict[str, Quote] | None = None) -> None:
        self._answers = answers or {}
        self.calls: list[frozenset[str]] = []

    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        self.calls.append(frozenset(tickers))
        return {t: q for t, q in self._answers.items() if t in tickers}


class RaisingProvider(MarketDataProvider):
    poll_interval_seconds = 0.0

    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        raise RuntimeError("boom")


async def test_missing_ticker_marks_unavailable(price_cache: PriceCache, tracked_set_stub) -> None:
    # AAPL has a quote queued up; MSFT does not — simulates Massive omitting
    # a symbol from its response.
    provider = FakeProvider({"AAPL": Quote(ticker="AAPL", price=190.0)})
    tracked_set_stub.set({"AAPL", "MSFT"})

    await run_market_data_cycle(provider, price_cache, tracked_set_stub)

    assert price_cache.get("AAPL").status == TickerStatus.OK
    assert price_cache.get("AAPL").price == 190.0
    assert price_cache.get("MSFT").status == TickerStatus.UNAVAILABLE


async def test_ticker_with_no_answer_becomes_unavailable(
    price_cache: PriceCache, tracked_set_stub
) -> None:
    provider = FakeProvider({})
    tracked_set_stub.set({"AAPL"})

    await run_market_data_cycle(provider, price_cache, tracked_set_stub)

    # No quote was ever supplied for AAPL, so it's unavailable, not pending —
    # ensure_tracked() only guarantees visibility, not "ok".
    assert price_cache.get("AAPL").status == TickerStatus.UNAVAILABLE


async def test_whole_fetch_failure_leaves_cache_unchanged(
    price_cache: PriceCache, tracked_set_stub
) -> None:
    price_cache.update("AAPL", 190.0, now=1.0)
    tracked_set_stub.set({"AAPL"})
    provider = RaisingProvider()

    await run_market_data_cycle(provider, price_cache, tracked_set_stub)

    entry = price_cache.get("AAPL")
    assert entry.status == TickerStatus.OK  # not flipped to unavailable
    assert entry.price == 190.0
    assert entry.updated_at == 1.0  # untouched


async def test_dropped_ticker_is_removed_from_cache(
    price_cache: PriceCache, tracked_set_stub
) -> None:
    price_cache.update("AAPL", 190.0, now=1.0)
    tracked_set_stub.set(set())  # AAPL no longer tracked
    provider = FakeProvider({})

    await run_market_data_cycle(provider, price_cache, tracked_set_stub)

    assert price_cache.get("AAPL") is None


async def test_reference_price_and_kind_pass_through_to_cache(
    price_cache: PriceCache, tracked_set_stub
) -> None:
    tracked_set_stub.set({"AAPL"})
    provider = FakeProvider(
        {"AAPL": Quote(ticker="AAPL", price=190.12, reference_price=188.5, reference_kind=ReferenceKind.PREV_CLOSE)}
    )

    await run_market_data_cycle(provider, price_cache, tracked_set_stub)

    entry = price_cache.get("AAPL")
    assert entry.reference_price == 188.5
    assert entry.reference_kind == ReferenceKind.PREV_CLOSE


async def test_run_market_data_loop_calls_fetch_repeatedly(
    price_cache: PriceCache, tracked_set_stub
) -> None:
    provider = FakeProvider({"AAPL": Quote(ticker="AAPL", price=190.0)})
    tracked_set_stub.set({"AAPL"})

    task = asyncio.create_task(run_market_data_loop(provider, price_cache, tracked_set_stub))
    try:
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(provider.calls) >= 2
