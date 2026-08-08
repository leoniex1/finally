"""The shared, in-memory price cache — the single source of truth for "what
is the price of ticker X right now" (`PLAN.md` §6). Read by the SSE stream,
the history endpoint, portfolio valuation, and the trade executor; written
only by the active `MarketDataProvider`'s driver loop (`driver.py`).
"""

from __future__ import annotations

import time
from typing import Optional

from .models import CacheEntry, PricePoint, ReferenceKind, TickerStatus


class PriceCache:
    """
    In-memory store of the latest known state for every tracked ticker.

    Concurrency model: this app runs a single asyncio event loop with no
    threads touching the cache. Every method here is synchronous and
    contains no `await`, so each call is atomic with respect to the loop —
    two coroutines can never interleave in the middle of a mutation. This is
    why no asyncio.Lock is used. If the app ever moves to multiple worker
    processes, the cache must move to a shared store (e.g., Redis) — it does
    not survive process boundaries today.
    """

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}

    def ensure_tracked(self, ticker: str) -> CacheEntry:
        """Idempotently register a ticker as tracked, defaulting to PENDING."""
        entry = self._entries.get(ticker)
        if entry is None:
            entry = CacheEntry(ticker=ticker)
            self._entries[ticker] = entry
        return entry

    def drop_untracked(self, still_tracked: set[str]) -> None:
        """Remove cache entries for tickers no longer in the tracked set."""
        for ticker in list(self._entries):
            if ticker not in still_tracked:
                del self._entries[ticker]

    def update(
        self,
        ticker: str,
        price: float,
        *,
        reference_price: Optional[float] = None,
        reference_kind: Optional[ReferenceKind] = None,
        now: Optional[float] = None,
    ) -> None:
        """
        Record a new price tick for a tracked ticker. Called by the shared
        driver loop (`driver.py`) once per observed `Quote`.

        If `reference_price` is omitted, the entry keeps its existing
        reference (or, on first tick, adopts `price` itself as a
        session-open reference — see `_seed_reference`).

        `updated_at` and `history` only advance on an actual price change —
        this is the invariant the SSE stream's diffing relies on ("emit only
        when `updated_at` has advanced"). A repeated identical price (common
        at low-priced tickers' diffusive step sizes, or a Massive poll that
        echoes the same last trade) still refreshes `status`/reference data
        but must not manufacture a fake tick.
        """
        now = now if now is not None else time.time()
        entry = self.ensure_tracked(ticker)

        price_changed = entry.price is None or entry.price != price

        entry.prev_price = entry.price if entry.price is not None else price
        entry.price = price
        entry.status = TickerStatus.OK
        if price_changed:
            entry.updated_at = now
            entry.history.append(PricePoint(t=now, price=price))

        if reference_price is not None:
            entry.reference_price = reference_price
            entry.reference_kind = reference_kind or ReferenceKind.PREV_CLOSE
        elif entry.reference_price is None:
            self._seed_reference(entry, price)

    def _seed_reference(self, entry: CacheEntry, price: float) -> None:
        entry.reference_price = price
        entry.reference_kind = ReferenceKind.SESSION_OPEN

    def mark_unavailable(self, ticker: str) -> None:
        """Source polled but returned no data for this ticker (still tracked)."""
        entry = self.ensure_tracked(ticker)
        entry.status = TickerStatus.UNAVAILABLE

    def get(self, ticker: str) -> Optional[CacheEntry]:
        return self._entries.get(ticker)

    def snapshot(self) -> list[CacheEntry]:
        """A point-in-time list of all tracked entries, for SSE initial connect."""
        return list(self._entries.values())

    def tracked_tickers(self) -> set[str]:
        return set(self._entries.keys())
