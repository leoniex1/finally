"""Database creation and seeding (`PLAN.md` §7).

Covers the `PLAN.md` §12 startup bullet — "schema exists and is seeded
before background tasks run — the snapshot task never sees a missing
table" — at the `init_db()` level; `test_main.py` covers the same
guarantee through the real lifespan.
"""

from __future__ import annotations

import os

import aiosqlite
import pytest

from app.db.init import DEFAULT_CASH_BALANCE, DEFAULT_WATCHLIST, init_db

EXPECTED_TABLES = {
    "users_profile",
    "watchlist",
    "positions",
    "trades",
    "portfolio_snapshots",
    "chat_messages",
}


async def _tables(db_path: str) -> set[str]:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in await cursor.fetchall()}


async def _rows(db_path: str, sql: str, params: tuple = ()) -> list[tuple]:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(sql, params)
        return list(await cursor.fetchall())


# ── Schema ───────────────────────────────────────────────────────────────


async def test_creates_every_table_in_the_plan(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path)

    assert EXPECTED_TABLES <= await _tables(path)


async def test_creates_the_snapshot_index(tmp_path) -> None:
    # PLAN.md §7 calls for an index on (user_id, recorded_at); the P&L
    # chart's windowed reads depend on it.
    path = str(tmp_path / "finally.db")
    await init_db(path)

    rows = await _rows(
        path,
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'portfolio_snapshots'",
    )
    assert any("user_recorded" in row[0] for row in rows)


async def test_creates_the_parent_directory(tmp_path) -> None:
    # `DB_PATH` routinely points into a directory that does not exist yet
    # (the repo's db/ on a local run, a freshly mounted volume in Docker).
    path = str(tmp_path / "nested" / "deeper" / "finally.db")
    await init_db(path)

    assert os.path.exists(path)


async def test_positions_ticker_is_unique_per_user(tmp_path) -> None:
    # One row per ticker per user (PLAN.md §7) — the upsert in the trade
    # executor relies on this constraint existing.
    path = str(tmp_path / "finally.db")
    await init_db(path)

    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO positions VALUES ('1', 'default', 'AAPL', 5.0, 100.0, 't')"
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO positions VALUES ('2', 'default', 'AAPL', 3.0, 110.0, 't')"
            )


async def test_trade_side_is_constrained_to_buy_or_sell(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path)

    async with aiosqlite.connect(path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO trades VALUES ('1', 'default', 'AAPL', 'sell_all', 1.0, 100.0, 't')"
            )


# ── Seeding ──────────────────────────────────────────────────────────────


async def test_seeds_the_default_cash_balance(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path)

    rows = await _rows(path, "SELECT cash_balance FROM users_profile WHERE id = 'default'")
    assert rows == [(DEFAULT_CASH_BALANCE,)]
    assert DEFAULT_CASH_BALANCE == 10000.0


async def test_seeds_the_ten_default_watchlist_tickers(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path)

    rows = await _rows(path, "SELECT ticker FROM watchlist WHERE user_id = 'default'")
    assert {row[0] for row in rows} == set(DEFAULT_WATCHLIST)
    assert len(DEFAULT_WATCHLIST) == 10


async def test_default_watchlist_matches_the_plan(tmp_path) -> None:
    assert DEFAULT_WATCHLIST == (
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


async def test_seeded_rows_have_unique_ids(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path)

    rows = await _rows(path, "SELECT id FROM watchlist")
    assert len({row[0] for row in rows}) == len(rows)


# ── Idempotence ──────────────────────────────────────────────────────────


async def test_running_twice_does_not_duplicate_seed_data(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path)
    await init_db(path)

    rows = await _rows(path, "SELECT COUNT(*) FROM watchlist")
    assert rows[0][0] == len(DEFAULT_WATCHLIST)

    profiles = await _rows(path, "SELECT COUNT(*) FROM users_profile")
    assert profiles[0][0] == 1


async def test_restart_preserves_user_changes(tmp_path) -> None:
    """A user who removes tickers and spends cash must not have the
    defaults silently restored on the next startup — which is why seeding
    is gated on the profile row, not on the watchlist being empty."""
    path = str(tmp_path / "finally.db")
    await init_db(path)

    async with aiosqlite.connect(path) as db:
        await db.execute("DELETE FROM watchlist")
        await db.execute("UPDATE users_profile SET cash_balance = 42.0 WHERE id = 'default'")
        await db.commit()

    await init_db(path)

    assert (await _rows(path, "SELECT COUNT(*) FROM watchlist"))[0][0] == 0
    assert (await _rows(path, "SELECT cash_balance FROM users_profile"))[0][0] == 42.0


async def test_existing_data_survives_a_schema_reapply(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path)

    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO positions VALUES ('p1', 'default', 'NVDA', 10.0, 120.0, 't')"
        )
        await db.commit()

    await init_db(path)

    rows = await _rows(path, "SELECT ticker, quantity FROM positions")
    assert rows == [("NVDA", 10.0)]


async def test_isolated_by_user_id(tmp_path) -> None:
    path = str(tmp_path / "finally.db")
    await init_db(path, user_id="someone-else")

    rows = await _rows(path, "SELECT user_id FROM watchlist")
    assert {row[0] for row in rows} == {"someone-else"}
