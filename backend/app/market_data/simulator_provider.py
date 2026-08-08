"""`SimulatorProvider` — a thin `MarketDataProvider` adapter over
`SimulatorEngine` (`MARKET_INTERFACE.md` §4.1). Owns none of the GBM math,
only the interface contract.
"""

from __future__ import annotations

from typing import AbstractSet, Mapping

from .provider import MarketDataProvider, Quote
from .simulator_engine import TICK_INTERVAL_SECONDS, SimulatorEngine


class SimulatorProvider(MarketDataProvider):
    poll_interval_seconds: float = TICK_INTERVAL_SECONDS

    def __init__(self) -> None:
        self._engine = SimulatorEngine()

    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        # Pure CPU-bound math, no I/O — safe to call directly from the
        # driver's async loop without a thread hop.
        self._engine.sync_tracked(tickers)
        prices = self._engine.step(tickers)  # {ticker: float}, one entry per input ticker

        # No reference_price/reference_kind here on purpose: the simulator
        # never has a real previous close, so it always wants the cache's
        # own "first price observed becomes the session-open reference"
        # behavior rather than duplicating that bookkeeping here. This also
        # means a ticker that drops out of the tracked set and later
        # rejoins gets a fresh session-open reference at whatever price it
        # restarts from — correct, since its old reference no longer
        # describes anything.
        return {ticker: Quote(ticker=ticker, price=price) for ticker, price in prices.items()}
