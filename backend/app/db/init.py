"""Database creation and seeding (`PLAN.md` §7).

Runs in the FastAPI lifespan handler, before any background task starts and
before any request is served. There is no separate migration step: the
schema is idempotent DDL applied on every startup, so a fresh Docker volume
comes up clean and seeded with no manual setup.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger("db.init")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

DEFAULT_USER_ID = "default"
DEFAULT_CASH_BALANCE = 10000.0

#: `PLAN.md` §7, "Default Seed Data".
DEFAULT_WATCHLIST = (
    "AAPL",
    "GOOGL",
    "MSFT",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
    "JPM",
    "V",
    "NFLX",
)


def utc_now_iso() -> str:
    """The one timestamp format written to the database: ISO-8601 UTC *with*
    offset (`API_CONTRACT.md` §2). Every repository writes through this so
    stored timestamps sort lexicographically — the snapshot window and the
    chat history both rely on string comparison in SQL."""
    return datetime.now(timezone.utc).isoformat()


async def init_db(db_path: str, user_id: str = DEFAULT_USER_ID) -> None:
    """
    Create the schema if it is missing and seed default data on a fresh
    database. Safe to call on every startup.

    Seeding is gated on the *user profile* being absent, not on the
    watchlist being empty. That distinction matters: a user who deliberately
    removes all ten default tickers must not have them silently restored on
    the next restart. A brand-new database (or a fresh Docker volume) has no
    profile row, so it gets both the $10,000 balance and the default
    watchlist; an existing one is left exactly as the user left it.
    """
    _ensure_parent_directory(db_path)

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

        if await _is_fresh(db, user_id):
            await _seed(db, user_id)
            logger.info(
                "seeded fresh database at %s: $%.2f cash, %d watchlist tickers",
                db_path,
                DEFAULT_CASH_BALANCE,
                len(DEFAULT_WATCHLIST),
            )

        await db.commit()


def _ensure_parent_directory(db_path: str) -> None:
    """`DB_PATH` may point into a directory that does not exist yet (the
    repo's `db/` on a local run, or a freshly mounted volume). SQLite will
    not create it, so an absent parent is an unhelpful "unable to open
    database file" rather than a missing directory."""
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)


async def _is_fresh(db: aiosqlite.Connection, user_id: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM users_profile WHERE id = ? LIMIT 1", (user_id,)
    )
    return await cursor.fetchone() is None


async def _seed(db: aiosqlite.Connection, user_id: str) -> None:
    now = utc_now_iso()

    await db.execute(
        "INSERT OR IGNORE INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
        (user_id, DEFAULT_CASH_BALANCE, now),
    )
    # OR IGNORE, not a plain INSERT: a database can legitimately hold
    # watchlist rows without a profile row (one written by an earlier
    # partial run, or by a test fixture). Colliding with the
    # (user_id, ticker) unique constraint must not take startup down.
    await db.executemany(
        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
        [(str(uuid.uuid4()), user_id, ticker, now) for ticker in DEFAULT_WATCHLIST],
    )
