"""The shared driver loop (`MARKET_INTERFACE.md` §5). One function drives
*either* provider — this is what keeps `SimulatorProvider` and
`MassiveProvider` fully decoupled from the cache and the tracked set.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .cache import PriceCache
from .provider import MarketDataProvider
from .tracked_set import TrackedSetProvider

logger = logging.getLogger("market_data.driver")


async def run_market_data_loop(
    provider: MarketDataProvider,
    cache: PriceCache,
    tracked_set: TrackedSetProvider,
) -> None:
    """
    Runs forever: recompute the tracked set, ask the provider for a fresh
    `fetch()` of exactly that set, and reconcile the result into the shared
    cache. Wrapped by `run_supervised()` (`supervisor.py`) so a transient
    exception here (e.g. a Massive network blip) restarts this loop with
    backoff instead of freezing the cache forever.
    """
    while True:
        await run_market_data_cycle(provider, cache, tracked_set)
        await asyncio.sleep(provider.poll_interval_seconds)


async def run_market_data_cycle(
    provider: MarketDataProvider,
    cache: PriceCache,
    tracked_set: TrackedSetProvider,
) -> None:
    """One iteration of the driver loop, with no sleep — factored out so
    tests can exercise a single cycle deterministically."""
    tracked = await tracked_set.get()
    cache.drop_untracked(tracked)
    for ticker in tracked:
        cache.ensure_tracked(ticker)  # visible as "pending" immediately

    try:
        quotes = await provider.fetch(frozenset(tracked))
    except Exception:
        # Whole-request failure: leave the cache exactly as it was and
        # retry next cycle. This mirrors MASSIVE_API.md §7's rule that a
        # transport failure must never be conflated with a per-ticker
        # "no data" result, which IS handled below via `quotes.get(...)`.
        logger.warning(
            "%s.fetch() failed; cache left unchanged this cycle",
            type(provider).__name__,
            exc_info=True,
        )
        return

    now = time.time()
    for ticker in tracked:
        quote = quotes.get(ticker)
        if quote is None:
            cache.mark_unavailable(ticker)
        else:
            cache.update(
                ticker,
                quote.price,
                reference_price=quote.reference_price,
                reference_kind=quote.reference_kind,
                now=now,
            )
