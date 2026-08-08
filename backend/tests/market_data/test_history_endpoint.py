"""`GET /api/prices/{ticker}/history` (`PLAN.md` §8,
`market-data-design.md` §13).

Exercised over real HTTP through an in-process ASGI transport, so the
status codes, path parameter handling, and JSON shape the frontend codes
against are all covered — not just the handler's return value.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes.market_data import router
from app.market_data.cache import PriceCache
from app.market_data.models import HISTORY_MAXLEN, ReferenceKind


@pytest.fixture
def app(price_cache: PriceCache) -> FastAPI:
    # A bare app rather than `create_app()`: these tests are about the
    # route, and the real lifespan would start the driver loop against a
    # database that does not exist in a unit test.
    application = FastAPI()
    application.state.price_cache = price_cache
    application.include_router(router)
    return application


@pytest.fixture
async def client(app: FastAPI):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def test_untracked_ticker_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/prices/AAPL/history")

    assert response.status_code == 404
    assert "not tracked" in response.json()["detail"]


async def test_tracked_ticker_returns_its_points_oldest_first(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("AAPL", 191.0, now=101.0)
    price_cache.update("AAPL", 189.5, now=102.0)

    response = await client.get("/api/prices/AAPL/history")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AAPL"
    assert [p["price"] for p in body["points"]] == [190.0, 191.0, 189.5]
    assert [p["t"] for p in body["points"]] == [100.0, 101.0, 102.0]


async def test_response_carries_the_reference_baseline(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    # The chart needs the baseline to draw the change-% reference line, and
    # `reference_kind` is what lets the UI honestly label it (PLAN.md §10).
    price_cache.update(
        "AAPL",
        190.0,
        reference_price=188.5,
        reference_kind=ReferenceKind.PREV_CLOSE,
        now=100.0,
    )

    body = (await client.get("/api/prices/AAPL/history")).json()

    assert body["reference_price"] == 188.5
    assert body["reference_kind"] == "prev_close"


async def test_simulator_sourced_ticker_reports_session_open(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)  # no reference supplied

    body = (await client.get("/api/prices/AAPL/history")).json()

    assert body["reference_price"] == 190.0
    assert body["reference_kind"] == "session_open"


async def test_pending_ticker_returns_200_with_no_points(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    # A fresh container legitimately has an empty buffer — that is a 200
    # with `points: []`, not an error. The chart fills in from SSE.
    price_cache.ensure_tracked("AAPL")

    response = await client.get("/api/prices/AAPL/history")

    assert response.status_code == 200
    body = response.json()
    assert body["points"] == []
    assert body["reference_price"] is None
    assert body["reference_kind"] is None


async def test_ticker_is_normalized_to_uppercase(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    response = await client.get("/api/prices/aapl/history")

    assert response.status_code == 200
    assert response.json()["ticker"] == "AAPL"


async def test_points_are_capped_at_the_ring_buffer_size(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    # Confirms the deque(maxlen=...) wiring actually bounds what the
    # endpoint serves — the buffer is the response's only bound.
    for i in range(HISTORY_MAXLEN + 500):
        price_cache.update("AAPL", 100.0 + i, now=float(i))

    body = (await client.get("/api/prices/AAPL/history")).json()

    assert len(body["points"]) == HISTORY_MAXLEN
    # Oldest points were evicted, newest retained.
    assert body["points"][-1]["price"] == 100.0 + HISTORY_MAXLEN + 499
    assert body["points"][0]["price"] == 100.0 + 500


async def test_duplicate_prices_do_not_consume_buffer_slots(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    """Finding #1, observed from the history endpoint's side: the backfill
    covers real wall-clock time instead of being padded out with repeats
    of the same rounded price."""
    price_cache.update("CHEAP", 30.28, now=1.0)
    for i in range(2, 100):
        price_cache.update("CHEAP", 30.28, now=float(i))
    price_cache.update("CHEAP", 30.29, now=100.0)

    body = (await client.get("/api/prices/CHEAP/history")).json()

    assert [p["price"] for p in body["points"]] == [30.28, 30.29]


async def test_dropped_ticker_stops_being_served(
    client: httpx.AsyncClient, price_cache: PriceCache
) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    assert (await client.get("/api/prices/AAPL/history")).status_code == 200

    price_cache.drop_untracked(set())

    assert (await client.get("/api/prices/AAPL/history")).status_code == 404
