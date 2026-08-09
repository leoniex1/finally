"""`trades_repo` (`API_CONTRACT.md` §§3.3, 4.2)."""

from __future__ import annotations

import aiosqlite
import pytest

from app.db import trades_repo
from app.db.connection import transaction


async def test_insert_returns_the_persisted_row(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        trade = await trades_repo.insert_trade(db, "AAPL", "buy", 10.0, 201.78)

    assert (trade.ticker, trade.side, trade.quantity, trade.price) == (
        "AAPL",
        "buy",
        10.0,
        201.78,
    )
    assert trade.id and trade.executed_at

    stored = await trades_repo.list_trades(db)
    assert stored == [trade]


async def test_total_is_derived_from_quantity_and_price(
    db: aiosqlite.Connection,
) -> None:
    async with transaction(db):
        trade = await trades_repo.insert_trade(db, "AAPL", "buy", 10.0, 201.78)

    assert trade.total == pytest.approx(2017.80)
    assert trade.to_dict()["total"] == pytest.approx(2017.80)


async def test_rejects_a_side_outside_the_enum(db: aiosqlite.Connection) -> None:
    """A mis-parsed LLM response must not land in the log as an
    uninterpretable trade — the schema CHECK is the last line of defence."""
    with pytest.raises(aiosqlite.IntegrityError):
        async with transaction(db):
            await trades_repo.insert_trade(db, "AAPL", "sell_all", 1.0, 100.0)

    assert await trades_repo.list_trades(db) == []


async def test_lists_newest_first(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        for ticker in ("AAPL", "NVDA", "MSFT"):
            await trades_repo.insert_trade(db, ticker, "buy", 1.0, 100.0)

    assert [t.ticker for t in await trades_repo.list_trades(db)] == [
        "MSFT",
        "NVDA",
        "AAPL",
    ]


async def test_ties_on_executed_at_fall_back_to_insertion_order(
    db: aiosqlite.Connection,
) -> None:
    """Several fills in one chat turn can share a timestamp to the
    microsecond; `rowid` is then the only ordering left."""
    async with transaction(db):
        for ticker in ("AAPL", "NVDA", "MSFT"):
            await db.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
                (ticker, "default", ticker, "buy", 1.0, 100.0, "2026-08-08T00:00:00+00:00"),
            )

    assert [t.ticker for t in await trades_repo.list_trades(db)] == [
        "MSFT",
        "NVDA",
        "AAPL",
    ]


async def test_honours_the_limit(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        for _ in range(5):
            await trades_repo.insert_trade(db, "AAPL", "buy", 1.0, 100.0)

    assert len(await trades_repo.list_trades(db, limit=2)) == 2


async def test_scoped_to_the_user(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await trades_repo.insert_trade(db, "AAPL", "buy", 1.0, 100.0, user_id="other")

    assert await trades_repo.list_trades(db) == []
    assert len(await trades_repo.list_trades(db, user_id="other")) == 1


async def test_to_dict_omits_the_internal_user_id(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        trade = await trades_repo.insert_trade(db, "AAPL", "buy", 10.0, 201.78)

    assert set(trade.to_dict()) == {
        "id",
        "ticker",
        "side",
        "quantity",
        "price",
        "total",
        "executed_at",
    }
