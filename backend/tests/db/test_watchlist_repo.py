"""`watchlist_repo` (`API_CONTRACT.md` §§3.5–3.7, 4.2)."""

from __future__ import annotations

import aiosqlite

from app.db import watchlist_repo
from app.db.connection import transaction
from app.db.init import DEFAULT_WATCHLIST


async def test_lists_the_seeded_watchlist(db: aiosqlite.Connection) -> None:
    rows = await watchlist_repo.list_watchlist(db)
    assert {row.ticker for row in rows} == set(DEFAULT_WATCHLIST)


async def test_orders_by_added_at_ascending(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await db.execute("DELETE FROM watchlist")
        for ticker, added_at in (("ZZ", "2026-01-03T00:00:00+00:00"),
                                 ("AA", "2026-01-01T00:00:00+00:00"),
                                 ("MM", "2026-01-02T00:00:00+00:00")):
            await db.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?,?,?,?)",
                (ticker, "default", ticker, added_at),
            )

    rows = await watchlist_repo.list_watchlist(db)
    assert [row.ticker for row in rows] == ["AA", "MM", "ZZ"]


async def test_add_returns_the_new_row(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        row = await watchlist_repo.add_watchlist_ticker(db, "PYPL")

    assert row.ticker == "PYPL"
    assert row.user_id == "default"
    assert row.id and row.added_at

    rows = await watchlist_repo.list_watchlist(db)
    assert "PYPL" in {r.ticker for r in rows}


async def test_add_is_idempotent_and_preserves_the_original_added_at(
    db: aiosqlite.Connection,
) -> None:
    """Re-adding must not reshuffle the list under the user — the UI orders
    by `added_at` (`API_CONTRACT.md` §3.6)."""
    async with transaction(db):
        first = await watchlist_repo.add_watchlist_ticker(db, "PYPL")
    async with transaction(db):
        second = await watchlist_repo.add_watchlist_ticker(db, "PYPL")

    assert second.id == first.id
    assert second.added_at == first.added_at

    cursor = await db.execute(
        "SELECT COUNT(*) FROM watchlist WHERE ticker = 'PYPL'"
    )
    assert (await cursor.fetchone())[0] == 1


async def test_add_is_idempotent_against_a_seeded_ticker(
    db: aiosqlite.Connection,
) -> None:
    before = {row.ticker: row.added_at for row in await watchlist_repo.list_watchlist(db)}

    async with transaction(db):
        row = await watchlist_repo.add_watchlist_ticker(db, "AAPL")

    assert row.added_at == before["AAPL"]
    assert len(await watchlist_repo.list_watchlist(db)) == len(before)


async def test_remove_reports_whether_a_row_went(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        assert await watchlist_repo.remove_watchlist_ticker(db, "NFLX") is True

    tickers = {row.ticker for row in await watchlist_repo.list_watchlist(db)}
    assert "NFLX" not in tickers


async def test_remove_returns_false_for_an_absent_ticker(
    db: aiosqlite.Connection,
) -> None:
    """The route stays idempotent (`204` either way, §3.7) but the caller can
    still tell nothing changed."""
    async with transaction(db):
        assert await watchlist_repo.remove_watchlist_ticker(db, "ZZZZ") is False


async def test_remove_leaves_positions_alone(db: aiosqlite.Connection) -> None:
    """`PLAN.md` §6: a held ticker stays tracked through its open position,
    so removing the watchlist row must not touch `positions`."""
    async with transaction(db):
        await db.execute(
            "INSERT INTO positions VALUES ('p1','default','NFLX',10.0,500.0,'t')"
        )
        await watchlist_repo.remove_watchlist_ticker(db, "NFLX")

    cursor = await db.execute("SELECT quantity FROM positions WHERE ticker = 'NFLX'")
    assert (await cursor.fetchone())["quantity"] == 10.0


async def test_scoped_to_the_user(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await watchlist_repo.add_watchlist_ticker(db, "PYPL", user_id="someone-else")

    default_tickers = {row.ticker for row in await watchlist_repo.list_watchlist(db)}
    assert "PYPL" not in default_tickers

    other = await watchlist_repo.list_watchlist(db, user_id="someone-else")
    assert [row.ticker for row in other] == ["PYPL"]


async def test_to_dict_is_the_market_data_merge_base(db: aiosqlite.Connection) -> None:
    """§3.5 builds an entry as `{**row.to_dict(), **entry.to_sse_event()}` —
    the row half must carry no market-data keys and no `user_id`."""
    row = (await watchlist_repo.list_watchlist(db))[0]
    assert set(row.to_dict()) == {"ticker", "added_at"}
