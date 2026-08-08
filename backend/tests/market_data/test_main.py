"""FastAPI app construction and lifespan wiring (`market-data-design.md`
§11, `PLAN.md` §7).

Covers the `PLAN.md` §12 startup bullet — background tasks must not run
before the schema exists — plus the shutdown path that Finding #3 of
`planning/MARKET_DATA_REVIEW.md` called for.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite
import httpx
import pytest

from app.config import Settings
from app.db.init import DEFAULT_CASH_BALANCE, DEFAULT_WATCHLIST
from app.main import create_app
from app.market_data.cache import PriceCache
from app.market_data.massive_provider import MassiveProvider
from app.market_data.models import TickerStatus
from app.market_data.simulator_provider import SimulatorProvider

@pytest.fixture
def seeded_db(tmp_path) -> str:
    """A path for a database the lifespan will create and seed itself.

    The file deliberately does not exist yet: `init_db()` running inside
    the lifespan is the thing under test, so pre-creating a schema here
    would test a fixture instead of the app.
    """
    return str(tmp_path / "finally-test.db")


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
        assert await _wait_until(
            lambda: {e.ticker for e in cache.snapshot()} == set(DEFAULT_WATCHLIST)
        )
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


async def test_startup_creates_and_seeds_a_database_that_does_not_exist(tmp_path) -> None:
    """`PLAN.md` §7/§12: the schema exists and is seeded before the driver
    runs. A fresh Docker volume must come up as a working app with the ten
    default tickers already streaming — no manual setup, no first-request
    deferral."""
    db_path = str(tmp_path / "fresh" / "finally.db")
    app = create_app(_settings(db_path))

    async with running_app(app) as client:
        assert os.path.exists(db_path)

        cache: PriceCache = app.state.price_cache
        assert await _wait_until(
            lambda: {e.ticker for e in cache.snapshot()} == set(DEFAULT_WATCHLIST)
        )

        response = await client.get("/api/prices/NVDA/history")
        assert response.status_code == 200

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT cash_balance FROM users_profile WHERE id = 'default'")
        assert (await cursor.fetchone())[0] == DEFAULT_CASH_BALANCE


async def test_the_driver_never_sees_a_missing_table(tmp_path) -> None:
    """The ordering guarantee, asserted directly: `init_db` completes before
    the driver's first cycle, so the tracked-set query never runs against a
    schema-less database. A single logged failure here would mean startup
    order had regressed."""
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Capture(level=logging.WARNING)
    supervisor_log = logging.getLogger("market_data.supervisor")
    driver_log = logging.getLogger("market_data.driver")
    supervisor_log.addHandler(handler)
    driver_log.addHandler(handler)
    try:
        app = create_app(_settings(str(tmp_path / "fresh.db")))
        async with running_app(app):
            cache: PriceCache = app.state.price_cache
            assert await _wait_until(lambda: len(cache.snapshot()) == len(DEFAULT_WATCHLIST))
    finally:
        supervisor_log.removeHandler(handler)
        driver_log.removeHandler(handler)

    assert records == []


async def test_startup_stays_alive_if_the_schema_disappears_at_runtime(tmp_path) -> None:
    """The supervisor's job, exercised in situ rather than only in
    `test_supervisor.py`: a database that breaks *after* startup degrades to
    "prices stop updating", not "the app falls over"."""
    db_path = str(tmp_path / "finally.db")
    app = create_app(_settings(db_path))

    async with running_app(app) as client:
        cache: PriceCache = app.state.price_cache
        assert await _wait_until(lambda: len(cache.snapshot()) == len(DEFAULT_WATCHLIST))

        async with aiosqlite.connect(db_path) as db:
            await db.execute("DROP TABLE watchlist")
            await db.commit()

        await asyncio.sleep(0.2)  # several driver cycles against a broken DB
        assert (await client.get("/api/health")).status_code == 200


async def test_health_endpoint_reports_ok(tmp_path) -> None:
    app = create_app(_settings(str(tmp_path / "finally.db")))

    async with running_app(app) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_endpoint_is_registered(tmp_path) -> None:
    app = create_app(_settings(str(tmp_path / "finally.db")))

    assert "/api/health" in _route_paths(app)


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
        assert await _wait_until(lambda: len(cache.snapshot()) == len(DEFAULT_WATCHLIST))

    # No market_data task is left running after the app shuts down.
    remaining = [t for t in asyncio.all_tasks() if t.get_name() == "market_data" and not t.done()]
    assert remaining == []
