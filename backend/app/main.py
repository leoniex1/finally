"""FastAPI application and lifespan wiring (`market-data-design.md` §11).

This module owns only the market data subsystem's startup/shutdown. The
database schema/seed, the portfolio and chat routers, and static frontend
serving belong to other modules and plug in at the marked seams below.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI

from .api.routes import market_data as market_data_routes
from .config import Settings
from .market_data.cache import PriceCache
from .market_data.driver import run_market_data_loop
from .market_data.factory import build_market_data_provider
from .market_data.supervisor import run_supervised
from .market_data.tracked_set import TrackedSetProvider

logger = logging.getLogger("app.main")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the application. `settings` is injectable so tests can point
    the tracked set at a temporary database instead of `db/finally.db`."""
    resolved = settings if settings is not None else Settings.from_env()

    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.state.settings = resolved
    # Present from construction so a request that somehow arrives before
    # the lifespan has run gets an empty cache rather than an AttributeError.
    app.state.price_cache = PriceCache()
    app.include_router(market_data_routes.router)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup order is load-bearing (`PLAN.md` §7): the database must exist
    and be seeded *before* any background task runs, because the driver
    loop queries `watchlist`/`positions` on its very first cycle without
    waiting for a request.

    Order: database → cache/provider construction → supervised tasks →
    serve requests. Shutdown runs in reverse: cancel tasks, then release
    the provider's resources.
    """
    settings: Settings = app.state.settings

    # ── Database ─────────────────────────────────────────────────────────
    # `app/db/` (schema + seed) is owned by the database module's design
    # doc and does not exist yet. Its `await init_db(settings.db_path)` call
    # belongs HERE — before the tasks below start. Until it lands, a run
    # against a database with no `watchlist`/`positions` tables makes
    # `TrackedSetProvider.get()` raise; `run_supervised` catches that, logs
    # it, and retries with backoff, so the app still starts and serves.

    cache = PriceCache()
    tracked_set = TrackedSetProvider(settings.db_path)
    provider = build_market_data_provider(settings)

    # The single shared cache instance. The SSE route, the history route,
    # and (in the portfolio module) trade execution and `/api/portfolio`
    # all reach the same object through here.
    app.state.price_cache = cache
    app.state.market_data_provider = provider

    logger.info(
        "market data provider: %s (poll interval %.1fs)",
        type(provider).__name__,
        provider.poll_interval_seconds,
    )

    tasks = [
        asyncio.create_task(
            run_supervised(
                "market_data",
                lambda: run_market_data_loop(provider, cache, tracked_set),
            ),
            name="market_data",
        ),
        # The portfolio-snapshot task (`PLAN.md` §7) is wired the same way
        # here once the portfolio module lands.
    ]

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Provider-agnostic by design: `MarketDataProvider.aclose()` is a
        # no-op on the base class and closes the HTTP connection pool on
        # `MassiveProvider`, so no isinstance check is needed here.
        await provider.aclose()


app = create_app()
