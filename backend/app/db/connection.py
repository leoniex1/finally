"""SQLite connection and transaction management (`API_CONTRACT.md` §4.1).

Every write in the app funnels through `transaction()`, and every connection
through `connect()`, so the pragmas below are applied exactly once, in one
place. Nothing outside `app/db/` opens a connection.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger("db.connection")


@asynccontextmanager
async def connect(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """
    Open a connection with the app's standard pragmas, closing it on exit.

    - ``journal_mode=WAL`` lets the snapshot task read while a trade writes,
      instead of the two serialising on a single reader/writer lock.
    - ``busy_timeout=5000`` covers the case WAL does not: two *writers*
      (a trade and the 30-second snapshot loop) landing together. Without it
      the loser raises `database is locked` immediately rather than waiting.
    - ``foreign_keys=ON`` is per-connection in SQLite — it is off by default
      on every new connection, so it has to be set here, not in `schema.sql`.
    - ``row_factory`` is `aiosqlite.Row` so repositories can read columns by
      name and stay readable when the schema grows a column.

    **This context manager does not commit.** `sqlite3` rolls back an open
    transaction on close, so a caller that writes without wrapping the write
    in `transaction()` silently loses it. That is deliberate: the commit
    boundary belongs to the caller, because the trade path has to commit four
    statements as one unit (`API_CONTRACT.md` §3.3).
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


@asynccontextmanager
async def transaction(db: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """
    Commit on a clean exit, roll back on any exception.

    Compose as many repository calls inside one block as the operation needs
    — that is why every repository function takes the connection rather than
    a path. A trade inserts a `trades` row, upserts or deletes a `positions`
    row, updates the cash balance and inserts a snapshot; all four land or
    none do.

    Not reentrant: SQLite has no nested transactions, so an inner block would
    commit the outer block's half-finished work. One `transaction()` per
    logical operation, at the outermost level.
    """
    try:
        yield db
    except BaseException:
        # BaseException, not Exception: a cancelled trade coroutine
        # (asyncio.CancelledError) must not leave a half-written transaction
        # to be committed by whatever runs next on this connection.
        await db.rollback()
        raise
    await db.commit()
