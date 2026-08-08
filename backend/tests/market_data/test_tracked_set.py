from __future__ import annotations

import uuid

import aiosqlite
import pytest

from app.market_data.tracked_set import TrackedSetProvider

SCHEMA = """
CREATE TABLE watchlist (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    added_at TEXT,
    UNIQUE (user_id, ticker)
);

CREATE TABLE positions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL,
    updated_at TEXT,
    UNIQUE (user_id, ticker)
);
"""


@pytest.fixture
async def db_path(tmp_path) -> str:
    path = str(tmp_path / "finally-test.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    return path


async def _insert_watchlist(db_path: str, user_id: str, ticker: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, ticker, "2026-01-01T00:00:00"),
        )
        await db.commit()


async def _insert_position(db_path: str, user_id: str, ticker: str, quantity: float) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, ticker, quantity, 100.0, "2026-01-01T00:00:00"),
        )
        await db.commit()


async def test_empty_db_returns_empty_set(db_path: str) -> None:
    provider = TrackedSetProvider(db_path)
    assert await provider.get() == set()


async def test_watchlist_tickers_are_tracked(db_path: str) -> None:
    await _insert_watchlist(db_path, "default", "AAPL")
    await _insert_watchlist(db_path, "default", "MSFT")

    provider = TrackedSetProvider(db_path)
    assert await provider.get() == {"AAPL", "MSFT"}


async def test_held_ticker_removed_from_watchlist_stays_tracked(db_path: str) -> None:
    # A ticker with an open position but no watchlist row must still be tracked.
    await _insert_position(db_path, "default", "NVDA", quantity=10.0)

    provider = TrackedSetProvider(db_path)
    assert await provider.get() == {"NVDA"}


async def test_union_of_watchlist_and_positions(db_path: str) -> None:
    await _insert_watchlist(db_path, "default", "AAPL")
    await _insert_position(db_path, "default", "NVDA", quantity=5.0)
    await _insert_position(db_path, "default", "AAPL", quantity=2.0)  # overlaps watchlist

    provider = TrackedSetProvider(db_path)
    assert await provider.get() == {"AAPL", "NVDA"}


async def test_position_below_epsilon_is_not_tracked(db_path: str) -> None:
    # Residual float dust from a full sell (per PLAN.md §7's epsilon rule)
    # must not keep a ticker tracked forever.
    await _insert_position(db_path, "default", "TSLA", quantity=1e-13)

    provider = TrackedSetProvider(db_path)
    assert await provider.get() == set()


async def test_isolated_by_user_id(db_path: str) -> None:
    await _insert_watchlist(db_path, "default", "AAPL")
    await _insert_watchlist(db_path, "someone-else", "GOOGL")

    provider = TrackedSetProvider(db_path, user_id="default")
    assert await provider.get() == {"AAPL"}
