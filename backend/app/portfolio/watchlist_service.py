"""Watchlist reads and mutations (`API_CONTRACT.md` §3.5, §3.6, §3.7, §5).

Like `service.py`, this has two entry points — the REST routes and the LLM
action executor (`PLAN.md` §9) — so the rules live here once and both callers
share them, rejections included.

The thing worth understanding before changing anything in this module is what
a watchlist row *is not*: it is not the thing that makes a ticker priced.
The tracked set is `watchlist ∪ open positions`, recomputed from the database
every driver cycle by `TrackedSetProvider` (`PLAN.md` §6). So adding a row is
enough to start pricing a ticker — there is no registration call to make —
and removing one is *not* enough to stop pricing a ticker you still hold.
"""

from __future__ import annotations

from typing import Any, Optional

from ..db.connection import connect, transaction
from ..db.init import DEFAULT_USER_ID
from ..db.watchlist_repo import (
    add_watchlist_ticker,
    list_watchlist,
    remove_watchlist_ticker,
)
from ..market_data.cache import PriceCache
from ..market_data.models import TickerStatus
from ..db.rows import WatchlistRow
from .errors import WatchlistError
from .validation import normalize_ticker

#: What a `GET /api/watchlist` entry reports for a ticker the cache has never
#: seen — one awaiting its first tick, typically added moments ago. Mirrors
#: the field set of `CacheEntry.to_sse_event()` exactly so the frontend can
#: run one renderer over both shapes (`API_CONTRACT.md` §3.5).
_UNTRACKED_MARKET_FIELDS: dict[str, Any] = {
    "price": None,
    "prev_price": None,
    "reference_price": None,
    "reference_kind": None,
    "change_pct": None,
    "direction": "flat",
    "status": TickerStatus.PENDING.value,
    "updated_at": 0.0,
}


async def get_watchlist(
    db_path: str, cache: PriceCache, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    """The `GET /api/watchlist` body (`API_CONTRACT.md` §3.5)."""
    async with connect(db_path) as db:
        rows = await list_watchlist(db, user_id)

    return {"tickers": [_entry(row, cache) for row in rows]}


async def add_ticker(
    db_path: str,
    cache: PriceCache,
    ticker: Any,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """
    Add a ticker to the watchlist, returning its `GET /api/watchlist` entry.

    Idempotent — a ticker already on the list returns its existing row with
    the original `added_at`, so a re-submit does not reshuffle the UI order.
    The new ticker joins the tracked set on the next driver cycle (~500ms
    under the simulator), which is why the entry returned here almost always
    reports `status: "pending"` with a null price. That is correct, not a
    race: the frontend renders `—` until the first tick arrives over SSE.
    """
    symbol = _normalize(ticker)

    async with connect(db_path) as db:
        async with transaction(db):
            row = await add_watchlist_ticker(db, symbol, user_id)

    return _entry(row, cache)


async def remove_ticker(
    db_path: str, ticker: Any, user_id: str = DEFAULT_USER_ID
) -> None:
    """
    Remove a ticker from the watchlist. Idempotent — removing one that is not
    on the list is a no-op, and the route returns `204` either way (§3.7).

    Deliberately does not touch `positions`, and deliberately does not stop
    price tracking: a ticker you still hold stays in the tracked set through
    its open position. Portfolio value, unrealized P&L and every
    `portfolio_snapshots` row depend on that price continuing to arrive.
    """
    symbol = _normalize(ticker)

    async with connect(db_path) as db:
        async with transaction(db):
            await remove_watchlist_ticker(db, symbol, user_id)


def _entry(row: WatchlistRow, cache: PriceCache) -> dict[str, Any]:
    """A watchlist row merged with its live market data.

    The merge order matters: the database fields go down first and the cache
    event over the top, so `ticker` resolves to the cache's canonical symbol
    when an entry exists. Both are already normalized, so they agree — but
    fixing the precedence means they cannot disagree later.
    """
    entry = cache.get(row.ticker)
    market: dict[str, Any] = (
        entry.to_sse_event() if entry is not None else dict(_UNTRACKED_MARKET_FIELDS)
    )
    return {**row.to_dict(), "ticker": row.ticker, **market}


def _normalize(ticker: Any) -> str:
    """`normalize_ticker`'s `ValueError` carries the contract's `detail`
    string verbatim, so this never rewrites the message — a second copy of
    "Invalid ticker: ..." is a copy that can drift."""
    try:
        return normalize_ticker(ticker)
    except ValueError as exc:
        raise WatchlistError(400, str(exc)) from exc
