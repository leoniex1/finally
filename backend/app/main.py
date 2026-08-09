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

from .api.routes import chat as chat_routes
from .api.routes import market_data as market_data_routes
from .api.routes import portfolio as portfolio_routes
from .api.routes import watchlist as watchlist_routes
from .config import Settings
from .db.init import init_db
from .market_data.cache import PriceCache
from .market_data.driver import run_market_data_loop
from .market_data.factory import build_market_data_provider
from .market_data.supervisor import run_supervised
from .market_data.tracked_set import TrackedSetProvider
from .portfolio.snapshot_task import run_snapshot_loop
from .static_files import mount_static

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
    app.include_router(portfolio_routes.router)
    app.include_router(watchlist_routes.router)
    app.include_router(chat_routes.router)

    @app.get("/api/health")
    async def health() -> dict:
        """Liveness check for Docker/deployment (`PLAN.md` §8, System).

        Deliberately shallow: it reports that the process is up and serving,
        nothing more. It must not probe the database or the price cache — a
        health check that fails while the app is still serving requests
        would make a container restart loop out of a transient condition
        the supervisor is already designed to ride out.
        """
        return {"status": "ok"}

    # Last, unconditionally: `mount_static` installs a `/{full_path:path}`
    # catch-all, and FastAPI matches routes in registration order. Registered
    # any earlier it would swallow every `/api` route below it — including
    # the health check directly above — and serve them `index.html` with a
    # `200`, which is the kind of failure that looks like a frontend routing
    # bug for an hour before anyone suspects the mount order.
    mount_static(app, resolved.static_dir)

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
    # First, and awaited: the driver loop below queries `watchlist` and
    # `positions` on its very first cycle without waiting for a request, so
    # deferring this to the first request would leave a background task
    # hitting tables that do not exist yet.
    await init_db(settings.db_path)

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
        asyncio.create_task(
            run_supervised(
                "portfolio_snapshots",
                lambda: run_snapshot_loop(settings.db_path, cache),
            ),
            name="portfolio_snapshots",
        ),
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
