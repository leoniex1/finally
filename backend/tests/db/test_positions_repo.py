"""`positions_repo` (`API_CONTRACT.md` §4.2, `PLAN.md` §7)."""

from __future__ import annotations

import aiosqlite

from app.db import positions_repo
from app.db.connection import transaction


async def test_empty_on_a_fresh_database(db: aiosqlite.Connection) -> None:
    assert await positions_repo.list_positions(db) == []


async def test_upsert_then_get_round_trips(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        written = await positions_repo.upsert_position(db, "AAPL", 10.0, 190.0)

    read = await positions_repo.get_position(db, "AAPL")
    assert read == written
    assert read.quantity == 10.0
    assert read.avg_cost == 190.0


async def test_upsert_replaces_rather_than_accumulates(db: aiosqlite.Connection) -> None:
    """`quantity` and `avg_cost` are absolute state — the weighted-average
    math belongs to the trade service, not here."""
    async with transaction(db):
        await positions_repo.upsert_position(db, "AAPL", 10.0, 190.0)
        await positions_repo.upsert_position(db, "AAPL", 20.0, 195.89)

    row = await positions_repo.get_position(db, "AAPL")
    assert (row.quantity, row.avg_cost) == (20.0, 195.89)

    cursor = await db.execute("SELECT COUNT(*) FROM positions WHERE ticker = 'AAPL'")
    assert (await cursor.fetchone())[0] == 1


async def test_upsert_keeps_the_original_row_id(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        first = await positions_repo.upsert_position(db, "AAPL", 10.0, 190.0)
        second = await positions_repo.upsert_position(db, "AAPL", 11.0, 191.0)

    assert second.id == first.id
    assert second.updated_at >= first.updated_at


async def test_get_returns_none_when_flat(db: aiosqlite.Connection) -> None:
    assert await positions_repo.get_position(db, "AAPL") is None


async def test_delete_closes_the_position(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await positions_repo.upsert_position(db, "AAPL", 10.0, 190.0)
        await positions_repo.delete_position(db, "AAPL")

    assert await positions_repo.get_position(db, "AAPL") is None
    assert await positions_repo.list_positions(db) == []


async def test_delete_is_a_no_op_when_absent(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await positions_repo.delete_position(db, "AAPL")


async def test_delete_only_touches_the_named_ticker(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await positions_repo.upsert_position(db, "AAPL", 10.0, 190.0)
        await positions_repo.upsert_position(db, "NVDA", 5.0, 120.0)
        await positions_repo.delete_position(db, "AAPL")

    assert [row.ticker for row in await positions_repo.list_positions(db)] == ["NVDA"]


async def test_list_is_ticker_ordered(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        for ticker in ("NVDA", "AAPL", "MSFT"):
            await positions_repo.upsert_position(db, ticker, 1.0, 100.0)

    rows = await positions_repo.list_positions(db)
    assert [row.ticker for row in rows] == ["AAPL", "MSFT", "NVDA"]


async def test_supports_fractional_quantities(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await positions_repo.upsert_position(db, "AAPL", 0.25, 190.0)

    assert (await positions_repo.get_position(db, "AAPL")).quantity == 0.25


async def test_scoped_to_the_user(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await positions_repo.upsert_position(db, "AAPL", 10.0, 190.0, user_id="other")

    assert await positions_repo.get_position(db, "AAPL") is None
    assert (await positions_repo.get_position(db, "AAPL", user_id="other")).quantity == 10.0


async def test_to_dict_is_the_trade_response_position(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        row = await positions_repo.upsert_position(db, "AAPL", 20.0, 195.89)

    assert row.to_dict() == {
        "ticker": "AAPL",
        "quantity": 20.0,
        "avg_cost": 195.89,
        "updated_at": row.updated_at,
    }
