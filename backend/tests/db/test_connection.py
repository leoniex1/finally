"""Connection pragmas and transaction semantics (`API_CONTRACT.md` §4.1)."""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from app.db import positions_repo, profile_repo
from app.db.connection import connect, transaction


# ── Pragmas ──────────────────────────────────────────────────────────────


async def test_uses_wal_journal_mode(db_path: str) -> None:
    async with connect(db_path) as db:
        cursor = await db.execute("PRAGMA journal_mode")
        assert (await cursor.fetchone())[0] == "wal"


async def test_enables_foreign_keys(db_path: str) -> None:
    # Per-connection in SQLite and off by default, so it cannot be set in
    # schema.sql — only here.
    async with connect(db_path) as db:
        cursor = await db.execute("PRAGMA foreign_keys")
        assert (await cursor.fetchone())[0] == 1


async def test_sets_a_busy_timeout(db_path: str) -> None:
    # The 30-second snapshot loop and a trade can want the write lock at the
    # same moment; without this the loser fails instantly.
    async with connect(db_path) as db:
        cursor = await db.execute("PRAGMA busy_timeout")
        assert (await cursor.fetchone())[0] == 5000


async def test_rows_are_accessible_by_column_name(db_path: str) -> None:
    async with connect(db_path) as db:
        cursor = await db.execute("SELECT cash_balance FROM users_profile")
        record = await cursor.fetchone()
        assert record["cash_balance"] == 10000.0


# ── transaction() ────────────────────────────────────────────────────────


async def test_commits_on_clean_exit(db_path: str) -> None:
    async with connect(db_path) as db:
        async with transaction(db):
            await profile_repo.set_cash_balance(db, 4321.0)

    async with connect(db_path) as db:
        assert await profile_repo.get_cash_balance(db) == 4321.0


async def test_rolls_back_every_write_on_an_exception(db_path: str) -> None:
    """The trade path's guarantee: all four writes land or none do
    (`API_CONTRACT.md` §3.3)."""

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with connect(db_path) as db:
            async with transaction(db):
                await profile_repo.set_cash_balance(db, 1.0)
                await positions_repo.upsert_position(db, "AAPL", 10.0, 190.0)
                raise Boom

    async with connect(db_path) as db:
        assert await profile_repo.get_cash_balance(db) == 10000.0
        assert await positions_repo.get_position(db, "AAPL") is None


async def test_rolls_back_on_cancellation(db_path: str) -> None:
    """`CancelledError` is a BaseException — a cancelled request must not
    leave half a trade for the next `commit()` on the connection to pick up."""

    async def cancelled_write() -> None:
        async with connect(db_path) as db:
            async with transaction(db):
                await positions_repo.upsert_position(db, "NVDA", 5.0, 120.0)
                raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled_write()

    async with connect(db_path) as db:
        assert await positions_repo.get_position(db, "NVDA") is None


async def test_writes_outside_a_transaction_do_not_persist(db_path: str) -> None:
    """Documenting the sharp edge in `connect()`'s docstring: closing a
    connection rolls back, so an unwrapped write is silently lost."""
    async with connect(db_path) as db:
        await positions_repo.upsert_position(db, "TSLA", 1.0, 200.0)

    async with connect(db_path) as db:
        assert await positions_repo.get_position(db, "TSLA") is None


async def test_a_later_transaction_is_unaffected_by_an_earlier_rollback(
    db_path: str,
) -> None:
    async with connect(db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            async with transaction(db):
                await db.execute(
                    "INSERT INTO trades VALUES ('t1','default','AAPL','hodl',1.0,1.0,'t')"
                )

        async with transaction(db):
            await profile_repo.set_cash_balance(db, 500.0)

    async with connect(db_path) as db:
        assert await profile_repo.get_cash_balance(db) == 500.0
        cursor = await db.execute("SELECT COUNT(*) FROM trades")
        assert (await cursor.fetchone())[0] == 0
