"""FastAPI app construction and lifespan wiring (`market-data-design.md`
§11, `PLAN.md` §7).

Covers the `PLAN.md` §12 startup bullet — background tasks must not run
before the schema exists — plus the shutdown path that Finding #3 of
`planning/MARKET_DATA_REVIEW.md` called for.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite
import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.market_data.cache import PriceCache
from app.market_data.massive_provider import MassiveProvider
from app.market_data.models import TickerStatus
from app.market_data.simulator_provider import SimulatorProvider

SCHEMA = """
CREATE TABLE watchlist (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    added_at TEXT,
    UNIQUE (user_id, ticker)
);

CREATE TABLE positions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL,
    updated_at TEXT,
    UNIQUE (user_id, ticker)
);
"""


@pytest.fixture
async def seeded_db(tmp_path) -> str:
    """A database shaped like the one `app/db/` will create, seeded with a
    couple of watchlist rows so the driver has something to price."""
    path = str(tmp_path / "finally-test.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        for ticker in ("AAPL", "MSFT"):
            await db.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), "default", ticker, "2026-01-01T00:00:00"),
            )
        await db.commit()
    return path


def _settings(db_path: str, **overrides) -> Settings:
    return Settings(db_path=db_path, **overrides)


def _route_paths(app) -> set[str]:
    """The app's public paths, read from the OpenAPI schema. FastAPI wraps
    `include_router` results in an internal router object whose shape has
    changed across versions, so `app.routes` is the wrong thing to walk;
    the schema is the stable, public view of the same information."""
    return set(app.openapi()["paths"])


@asynccontextmanager
async def running_app(app) -> AsyncIterator[httpx.AsyncClient]:
    """Start the app for real — lifespan included — and hand back a client
    bound to it. `httpx.ASGITransport` alone never runs startup/shutdown,
    which is precisely what these tests are about."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _wait_until(predicate, timeout: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# ── Construction ─────────────────────────────────────────────────────────


def test_create_app_registers_the_market_data_routes(seeded_db: str) -> None:
    app = create_app(_settings(seeded_db))

    assert "/api/stream/prices" in _route_paths(app)
    assert "/api/prices/{ticker}/history" in _route_paths(app)


def test_create_app_uses_injected_settings(seeded_db: str) -> None:
    app = create_app(_settings(seeded_db, massive_poll_interval_seconds=3.0))

    assert app.state.settings.db_path == seeded_db
    assert app.state.settings.massive_poll_interval_seconds == 3.0


def test_price_cache_exists_before_the_lifespan_runs(seeded_db: str) -> None:
    # A request arriving before startup completes should see an empty cache,
    # not an AttributeError on app.state.
    app = create_app(_settings(seeded_db))

    assert isinstance(app.state.price_cache, PriceCache)
    assert app.state.price_cache.snapshot() == []


# ── Lifespan startup ─────────────────────────────────────────────────────


async def test_lifespan_starts_the_driver_and_populates_the_cache(seeded_db: str) -> None:
    app = create_app(_settings(seeded_db))

    async with running_app(app) as client:
        cache: PriceCache = app.state.price_cache
        assert await _wait_until(lambda: {e.ticker for e in cache.snapshot()} == {"AAPL", "MSFT"})
        assert all(e.status is TickerStatus.OK for e in cache.snapshot())

        # And the routes read that same live cache instance.
        response = await client.get("/api/prices/AAPL/history")
        assert response.status_code == 200
        assert response.json()["points"]


async def test_lifespan_replaces_the_placeholder_cache(seeded_db: str) -> None:
    app = create_app(_settings(seeded_db))
    placeholder = app.state.price_cache

    async with running_app(app):
        assert isinstance(app.state.price_cache, PriceCache)
        # The driver writes into whatever instance the routes read, so the
        # two must be the same object — the pre-startup placeholder is not
        # the one that ends up wired to the loop.
        assert await _wait_until(lambda: app.state.price_cache.snapshot() != [])
        assert placeholder.snapshot() == []


async def test_lifespan_selects_the_simulator_without_an_api_key(seeded_db: str) -> None:
    app = create_app(_settings(seeded_db))

    async with running_app(app):
        assert isinstance(app.state.market_data_provider, SimulatorProvider)


async def test_lifespan_selects_massive_with_an_api_key(seeded_db: str) -> None:
    app = create_app(_settings(seeded_db, massive_api_key="secret"))

    async with running_app(app):
        assert isinstance(app.state.market_data_provider, MassiveProvider)


async def test_startup_survives_a_database_with_no_tables(tmp_path) -> None:
    """Until `app/db/` lands, a missing schema must degrade to "prices stop
    updating", not "the app fails to start" — `run_supervised` absorbs the
    driver's exception and retries with backoff."""
    app = create_app(_settings(str(tmp_path / "empty.db")))

    async with running_app(app) as client:
        # The app serves; the untracked ticker just 404s.
        assert (await client.get("/api/prices/AAPL/history")).status_code == 404


# ── Lifespan shutdown ────────────────────────────────────────────────────


async def test_shutdown_closes_the_provider(seeded_db: str) -> None:
    """Finding #3: the lifespan closes whatever provider is active through
    the base interface, so Massive's connection pool is released."""
    app = create_app(_settings(seeded_db, massive_api_key="secret"))

    async with running_app(app):
        provider = app.state.market_data_provider
        assert provider._client.is_closed is False

    assert provider._client.is_closed is True


async def test_shutdown_closes_the_simulator_provider_too(seeded_db: str) -> None:
    # The no-op case still has to be exercised: the lifespan calls
    # `aclose()` unconditionally, so a provider without the hook would
    # raise AttributeError at shutdown.
    app = create_app(_settings(seeded_db))

    async with running_app(app):
        pass  # a clean shutdown is the assertion


async def test_shutdown_cancels_the_driver_task(seeded_db: str) -> None:
    app = create_app(_settings(seeded_db))

    async with running_app(app):
        cache: PriceCache = app.state.price_cache
        assert await _wait_until(lambda: len(cache.snapshot()) == 2)

    # No market_data task is left running after the app shuts down.
    remaining = [t for t in asyncio.all_tasks() if t.get_name() == "market_data" and not t.done()]
    assert remaining == []
