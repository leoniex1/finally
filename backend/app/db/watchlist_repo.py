"""Reads and writes for the `watchlist` table (`API_CONTRACT.md` §4.2).

Tickers arrive here already normalized — trim, uppercase, `^[A-Z]{1,5}$` —
by `app.portfolio.validation.normalize_ticker`. This layer deliberately does
not re-normalize: the `UNIQUE (user_id, ticker)` constraint that makes `add`
idempotent is case-sensitive, so a second copy of the rule that ever drifted
from the first would produce a watchlist holding both `AAPL` and `aapl`.

No function here commits. Wrap calls in `connection.transaction()`.
"""

from __future__ import annotations

import uuid

import aiosqlite

from .init import DEFAULT_USER_ID, utc_now_iso
from .rows import WatchlistRow


def _row(record: aiosqlite.Row) -> WatchlistRow:
    return WatchlistRow(
        id=record["id"],
        user_id=record["user_id"],
        ticker=record["ticker"],
        added_at=record["added_at"],
    )


async def list_watchlist(
    db: aiosqlite.Connection, user_id: str = DEFAULT_USER_ID
) -> list[WatchlistRow]:
    """Every watched ticker, oldest addition first (`API_CONTRACT.md` §3.5)."""
    cursor = await db.execute(
        "SELECT id, user_id, ticker, added_at FROM watchlist"
        " WHERE user_id = ? ORDER BY added_at ASC, ticker ASC",
        (user_id,),
    )
    return [_row(record) for record in await cursor.fetchall()]


async def add_watchlist_ticker(
    db: aiosqlite.Connection, ticker: str, user_id: str = DEFAULT_USER_ID
) -> WatchlistRow:
    """
    Add a ticker, returning its row. Idempotent: re-adding a ticker already
    on the list is a no-op that returns the *existing* row with its original
    `added_at` (`API_CONTRACT.md` §3.6).

    Preserving `added_at` is what keeps the watchlist from reshuffling under
    the user: the UI orders by it, so regenerating it would jump a
    re-submitted ticker to the bottom of a list it never left.
    """
    await db.execute(
        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, ticker, utc_now_iso()),
    )
    cursor = await db.execute(
        "SELECT id, user_id, ticker, added_at FROM watchlist WHERE user_id = ? AND ticker = ?",
        (user_id, ticker),
    )
    record = await cursor.fetchone()
    # The INSERT above either wrote this row or found it already there, so a
    # miss means the row vanished between two statements on one connection —
    # impossible, and worth failing loudly on rather than returning None and
    # widening the return type for every caller.
    assert record is not None, f"watchlist row for {ticker!r} disappeared mid-transaction"
    return _row(record)


async def remove_watchlist_ticker(
    db: aiosqlite.Connection, ticker: str, user_id: str = DEFAULT_USER_ID
) -> bool:
    """
    Remove a ticker from the watchlist. Returns whether a row actually went,
    so the route can stay idempotent (`204` either way, §3.7) while the
    caller still knows if anything changed.

    Touches `watchlist` only. A held ticker stays priced through its open
    position, because the tracked set is `watchlist ∪ open positions`
    (`PLAN.md` §6) — deleting the row here must never orphan a position's
    price.
    """
    cursor = await db.execute(
        "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
    )
    return cursor.rowcount > 0
