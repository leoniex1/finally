"""Background task supervision (`PLAN.md` §7): if a supervised coroutine
raises, log it and restart with exponential backoff rather than letting the
app run on with a frozen price cache."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger("market_data.supervisor")

INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


async def run_supervised(
    name: str,
    coro_factory: Callable[[], Awaitable[None]],
    *,
    initial_backoff_seconds: float = INITIAL_BACKOFF_SECONDS,
    max_backoff_seconds: float = MAX_BACKOFF_SECONDS,
) -> None:
    """
    Runs `coro_factory()` forever. If it raises, logs the exception and
    restarts it after an exponential backoff (capped), so one bad tick
    (e.g. a parsing bug, a transient exception) degrades to "prices stop
    updating for a few seconds" rather than killing the app's only price
    feed for the rest of the process lifetime.
    """
    backoff = initial_backoff_seconds
    while True:
        try:
            await coro_factory()
            # A well-behaved coroutine never returns; treat a clean return
            # as a bug too, and restart it rather than leaving the cache
            # frozen forever.
            logger.error("%s.run() returned unexpectedly; restarting", name)
        except asyncio.CancelledError:
            raise  # let shutdown propagate; do not swallow cancellation
        except Exception:
            logger.exception("%s crashed; restarting in %.1fs", name, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff_seconds)
