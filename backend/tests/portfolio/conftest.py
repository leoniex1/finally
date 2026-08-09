"""Fixtures for the portfolio service and route tests."""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.config import Settings
from app.db.init import init_db
from app.market_data.cache import PriceCache

#: NVDA is priced at exactly the figure in `API_CONTRACT.md` §3.8's worked
#: example: the `buy 100 nvda` mock trigger then costs $18,432.00 against a
#: $10,000 balance and fails on *insufficient cash*, which is the path
#: `PLAN.md` §12 names. Leave it unpriced and the same trigger fails on
#: `409 no price available` instead — still a failed action, but not the one
#: the scenario is written to exercise, and not what the real app does (NVDA
#: is a default watchlist ticker and is priced within a tick of startup).
SEED_PRICES = {"AAPL": 200.0, "GOOGL": 175.0, "MSFT": 400.0, "NVDA": 184.32}


@pytest.fixture
async def db_path(tmp_path) -> str:
    """A freshly initialized, seeded database — $10,000 cash, 10 tickers."""
    path = str(tmp_path / "finally.db")
    await init_db(path)
    return path


@pytest.fixture
def cache() -> PriceCache:
    """A price cache with a few tickers priced and ticking.

    Deliberately does *not* price every seeded watchlist ticker: the ones
    left out are what the `409 no price available` and `priced: false` paths
    are exercised against, and those are the cases most likely to be wrong.
    """
    populated = PriceCache()
    for ticker, price in SEED_PRICES.items():
        populated.update(ticker, price)
    return populated


@pytest.fixture
async def app_client(db_path: str, cache: PriceCache) -> AsyncIterator:
    """An HTTP client bound to the real app, with the background tasks and
    the lifespan bypassed.

    The lifespan is skipped on purpose: it would start the market data driver
    and the snapshot loop, making every route test race a live GBM simulator
    writing to the cache under it. Wiring `app.state` by hand gives the same
    routes over a deterministic cache.
    """
    import httpx

    from app.main import create_app

    app = create_app(Settings(db_path=db_path, llm_mock=True))
    app.state.price_cache = cache

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
