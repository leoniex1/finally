"""Watchlist rules (`API_CONTRACT.md` §3.5, §3.6, §3.7)."""

from __future__ import annotations

import pytest

from app.db.connection import connect
from app.db.positions_repo import get_position
from app.market_data.cache import PriceCache
from app.market_data.tracked_set import TrackedSetProvider
from app.portfolio.errors import WatchlistError
from app.portfolio.service import execute_trade
from app.portfolio.watchlist_service import add_ticker, get_watchlist, remove_ticker


async def _tickers(db_path: str, cache: PriceCache) -> list[str]:
    return [entry["ticker"] for entry in (await get_watchlist(db_path, cache))["tickers"]]


async def test_seeded_watchlist_is_returned_in_a_stable_order(
    db_path: str, cache: PriceCache
) -> None:
    """Ordering is `added_at` then `ticker` (§3.5).

    The ten seed rows are written in one statement and share a timestamp, so
    the tie-break decides — and it must be deterministic rather than SQLite's
    incidental row order, or the watchlist would reshuffle between restarts.
    """
    tickers = await _tickers(db_path, cache)

    assert len(tickers) == 10
    assert tickers == sorted(tickers)


async def test_a_later_addition_sorts_after_the_seeded_rows(
    db_path: str, cache: PriceCache
) -> None:
    await add_ticker(db_path, cache, "PYPL")

    assert (await _tickers(db_path, cache))[-1] == "PYPL"


async def test_entry_merges_live_market_data(db_path: str, cache: PriceCache) -> None:
    """§3.5: the market-data half is `CacheEntry.to_sse_event()` verbatim, so
    the frontend runs one renderer over both the REST payload and SSE."""
    entry = next(
        item
        for item in (await get_watchlist(db_path, cache))["tickers"]
        if item["ticker"] == "AAPL"
    )

    assert entry["price"] == 200.0
    assert entry["status"] == "ok"
    assert entry["reference_kind"] == "session_open"
    assert "added_at" in entry


async def test_unticked_ticker_reports_pending_with_null_price(
    db_path: str, cache: PriceCache
) -> None:
    """A ticker awaiting its first tick must report `pending` and a null
    price, so the UI renders `—` rather than a blank cell or a stale zero."""
    entry = next(
        item
        for item in (await get_watchlist(db_path, cache))["tickers"]
        if item["ticker"] == "NFLX"
    )

    assert entry["status"] == "pending"
    assert entry["price"] is None
    assert entry["change_pct"] is None


async def test_add_is_idempotent_and_preserves_added_at(
    db_path: str, cache: PriceCache
) -> None:
    """Regenerating `added_at` would jump a re-submitted ticker to the bottom
    of a list it never left — the UI orders by it."""
    first = await add_ticker(db_path, cache, "pypl")
    second = await add_ticker(db_path, cache, "PYPL")

    assert first["ticker"] == "PYPL"
    assert second["added_at"] == first["added_at"]
    assert (await _tickers(db_path, cache)).count("PYPL") == 1


@pytest.mark.parametrize("ticker", ["ABCDEF", "", "BRK.B", "12345", None, "  "])
async def test_add_rejects_malformed_tickers(
    db_path: str, cache: PriceCache, ticker
) -> None:
    with pytest.raises(WatchlistError) as caught:
        await add_ticker(db_path, cache, ticker)

    assert caught.value.status_code == 400
    assert "Invalid ticker" in caught.value.detail


async def test_add_normalizes_case_and_whitespace(
    db_path: str, cache: PriceCache
) -> None:
    entry = await add_ticker(db_path, cache, "  pypl  ")

    assert entry["ticker"] == "PYPL"


async def test_remove_is_idempotent(db_path: str, cache: PriceCache) -> None:
    await remove_ticker(db_path, "AAPL")
    # Second removal is a no-op, not an error — the route returns 204 either
    # way (§3.7).
    await remove_ticker(db_path, "AAPL")

    assert "AAPL" not in await _tickers(db_path, cache)


async def test_removing_a_held_ticker_leaves_the_position_and_keeps_it_tracked(
    db_path: str, cache: PriceCache
) -> None:
    """The scenario `PLAN.md` §6 exists to protect.

    The tracked set is `watchlist ∪ open positions`. If removing a watchlist
    row stopped price tracking for a ticker the user still holds, portfolio
    value, unrealized P&L and every snapshot would silently freeze at the
    last price seen.
    """
    await execute_trade(db_path, cache, "AAPL", 5, "buy")

    await remove_ticker(db_path, "AAPL")

    assert "AAPL" not in await _tickers(db_path, cache)
    async with connect(db_path) as db:
        assert (await get_position(db, "AAPL")).quantity == pytest.approx(5.0)
    assert "AAPL" in await TrackedSetProvider(db_path).get()


async def test_added_ticker_joins_the_tracked_set(
    db_path: str, cache: PriceCache
) -> None:
    """There is no register call — the driver recomputes the tracked set from
    the database, so the row itself is what starts pricing."""
    await add_ticker(db_path, cache, "PYPL")

    assert "PYPL" in await TrackedSetProvider(db_path).get()
