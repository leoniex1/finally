"""The trade ladder and portfolio valuation (`API_CONTRACT.md` §3.2, §3.3).

`execute_trade` is the only place a fill is ever written, so these tests are
weighted toward the two invariants that cannot be recovered from if they
break: a rejected trade must write **nothing**, and a fill must never happen
at a price the cache did not observe. Both failures are silent — they corrupt
`avg_cost` and the cash balance and leave no trace saying so.
"""

from __future__ import annotations

import pytest

from app.db.connection import connect
from app.db.positions_repo import get_position, list_positions
from app.db.profile_repo import get_cash_balance
from app.db.snapshots_repo import list_snapshots
from app.db.trades_repo import list_trades
from app.market_data.cache import PriceCache
from app.portfolio.errors import HistoryError, TradeError
from app.portfolio.service import (
    HISTORY_MAX_LIMIT,
    build_portfolio,
    compute_total_value,
    downsample,
    execute_trade,
    get_portfolio_history,
)


async def _state(db_path: str) -> tuple[float, list, list]:
    async with connect(db_path) as db:
        return (
            await get_cash_balance(db),
            await list_trades(db),
            await list_positions(db),
        )


# ── The §3.3 ladder ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("quantity", "side", "ticker", "status", "fragment"),
    [
        (0, "buy", "AAPL", 400, "Invalid quantity"),
        (-5, "buy", "AAPL", 400, "Invalid quantity"),
        (float("nan"), "buy", "AAPL", 400, "Invalid quantity"),
        (float("inf"), "buy", "AAPL", 400, "Invalid quantity"),
        ("ten", "buy", "AAPL", 400, "Invalid quantity"),
        (None, "buy", "AAPL", 400, "Invalid quantity"),
        (1, "hold", "AAPL", 400, "Invalid side"),
        (1, "BUY", "AAPL", 400, "Invalid side"),
        (1, "buy", "ABCDEF", 400, "Invalid ticker"),
        (1, "buy", "", 400, "Invalid ticker"),
        (1, "buy", "ZZZZ", 409, "No price available"),
        (10_000, "buy", "AAPL", 422, "Insufficient cash"),
        (1, "sell", "AAPL", 422, "Insufficient shares"),
    ],
)
async def test_rejections_carry_the_documented_status_and_write_nothing(
    db_path: str,
    cache: PriceCache,
    quantity,
    side,
    ticker,
    status: int,
    fragment: str,
) -> None:
    before = await _state(db_path)

    with pytest.raises(TradeError) as caught:
        await execute_trade(db_path, cache, ticker, quantity, side)

    assert caught.value.status_code == status
    assert fragment in caught.value.detail
    # The whole point: no trade row, no position row, no cash movement.
    assert await _state(db_path) == before


async def test_ladder_order_reports_quantity_before_side(
    db_path: str, cache: PriceCache
) -> None:
    """Order is normative (§3.3), so a doubly-invalid request reports the
    same reason every time rather than whichever check happened to run."""
    with pytest.raises(TradeError) as caught:
        await execute_trade(db_path, cache, "AAPL", -1, "hold")

    assert "Invalid quantity" in caught.value.detail


async def test_pending_ticker_is_refused_rather_than_filled_at_zero(
    db_path: str,
) -> None:
    """A ticker tracked but not yet ticked has `price is None`.

    This is the case `PLAN.md` §8 singles out: without the refusal the
    handler fills at `0.0`, granting free shares and corrupting `avg_cost`
    and cash permanently, with nothing downstream able to detect it.
    """
    cache = PriceCache()
    cache.ensure_tracked("NEWCO")

    with pytest.raises(TradeError) as caught:
        await execute_trade(db_path, cache, "NEWCO", 1, "buy")

    assert caught.value.status_code == 409
    assert (await _state(db_path))[1] == []


async def test_unavailable_ticker_is_refused(db_path: str) -> None:
    cache = PriceCache()
    cache.update("GONE", 50.0)
    cache.mark_unavailable("GONE")

    with pytest.raises(TradeError) as caught:
        await execute_trade(db_path, cache, "GONE", 1, "buy")

    assert caught.value.status_code == 409


# ── Fills ────────────────────────────────────────────────────────────────


async def test_buy_moves_cash_opens_a_position_and_snapshots(
    db_path: str, cache: PriceCache
) -> None:
    result = await execute_trade(db_path, cache, "AAPL", 10, "buy")

    assert result["trade"]["price"] == 200.0
    assert result["trade"]["total"] == 2000.0
    assert result["cash_balance"] == pytest.approx(8000.0)
    assert result["position"]["quantity"] == 10.0

    cash, trades, positions = await _state(db_path)
    assert cash == pytest.approx(8000.0)
    assert len(trades) == 1
    assert positions[0].avg_cost == pytest.approx(200.0)

    async with connect(db_path) as db:
        snapshots = await list_snapshots(db, "1970-01-01T00:00:00+00:00", 10)
    # §3.3: the snapshot is part of the same atomic unit, so the P&L chart
    # shows the step this fill produced instead of smoothing it away.
    assert len(snapshots) == 1
    assert snapshots[0].total_value == pytest.approx(10_000.0)


async def test_second_buy_blends_the_average_cost(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")
    cache.update("AAPL", 220.0)
    await execute_trade(db_path, cache, "AAPL", 10, "buy")

    async with connect(db_path) as db:
        position = await get_position(db, "AAPL")

    assert position.quantity == pytest.approx(20.0)
    assert position.avg_cost == pytest.approx(210.0)


async def test_partial_sell_keeps_avg_cost_and_credits_cash(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")
    cache.update("AAPL", 250.0)

    result = await execute_trade(db_path, cache, "AAPL", 4, "sell")

    assert result["cash_balance"] == pytest.approx(8000.0 + 1000.0)
    # `avg_cost` records what the *remaining* shares cost; selling some of
    # them does not change what the rest were bought for.
    assert result["position"]["avg_cost"] == pytest.approx(200.0)
    assert result["position"]["quantity"] == pytest.approx(6.0)


async def test_selling_at_a_loss_is_allowed(db_path: str, cache: PriceCache) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")
    cache.update("AAPL", 150.0)

    result = await execute_trade(db_path, cache, "AAPL", 10, "sell")

    assert result["cash_balance"] == pytest.approx(9500.0)
    assert result["position"] is None


async def test_full_liquidation_deletes_the_row(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")

    result = await execute_trade(db_path, cache, "AAPL", 10, "sell")

    assert result["position"] is None
    async with connect(db_path) as db:
        assert await get_position(db, "AAPL") is None


async def test_float_residue_below_epsilon_is_absorbed_not_left_behind(
    db_path: str, cache: PriceCache
) -> None:
    """`PLAN.md` §7: 'sell everything' arithmetic can leave `2e-13` behind.

    The epsilon absorbs it *into the fill* — the residual quantity is sold,
    so cash and the trade log stay consistent — and the row is deleted. A
    phantom 0-share holding in the table and the heatmap is the failure this
    prevents.
    """
    await execute_trade(db_path, cache, "AAPL", 10, "buy")

    result = await execute_trade(db_path, cache, "AAPL", 9.9999999, "sell")

    assert result["position"] is None
    assert result["trade"]["quantity"] == pytest.approx(10.0)
    assert result["cash_balance"] == pytest.approx(10_000.0)


async def test_sell_beyond_the_holding_is_rejected_not_clamped(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")
    before = await _state(db_path)

    with pytest.raises(TradeError) as caught:
        await execute_trade(db_path, cache, "AAPL", 11, "sell")

    assert caught.value.status_code == 422
    # Clamping would hand the user cash for shares they do not hold.
    assert await _state(db_path) == before


async def test_buy_using_exactly_the_whole_balance_is_allowed(
    db_path: str, cache: PriceCache
) -> None:
    """The cash check is `>`, not `>=` — spending the last dollar is legal."""
    result = await execute_trade(db_path, cache, "AAPL", 50, "buy")

    assert result["cash_balance"] == pytest.approx(0.0)


# ── Valuation ────────────────────────────────────────────────────────────


async def test_portfolio_values_positions_and_weights(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")
    cache.update("AAPL", 250.0)

    portfolio = await build_portfolio(db_path, cache)
    position = portfolio["positions"][0]

    assert portfolio["cash_balance"] == pytest.approx(8000.0)
    assert portfolio["positions_value"] == pytest.approx(2500.0)
    assert portfolio["total_value"] == pytest.approx(10_500.0)
    assert position["unrealized_pnl"] == pytest.approx(500.0)
    assert position["unrealized_pnl_pct"] == pytest.approx(0.25)
    assert position["priced"] is True
    # Weight is a share of the whole portfolio, cash included.
    assert position["weight"] == pytest.approx(2500.0 / 10_500.0)


async def test_unpriced_position_falls_back_to_cost_and_flags_itself(
    db_path: str, cache: PriceCache
) -> None:
    """§3.2: market value falls back to cost, and P&L is `null` rather than a
    fabricated `0.00` that would look like a real observation."""
    await execute_trade(db_path, cache, "AAPL", 10, "buy")
    cache.mark_unavailable("AAPL")

    portfolio = await build_portfolio(db_path, cache)
    position = portfolio["positions"][0]

    assert position["priced"] is False
    assert position["current_price"] is None
    assert position["unrealized_pnl"] is None
    assert position["unrealized_pnl_pct"] is None
    assert position["market_value"] == pytest.approx(2000.0)
    assert portfolio["total_value"] == pytest.approx(10_000.0)


async def test_empty_portfolio_reports_zeros_not_nulls(
    db_path: str, cache: PriceCache
) -> None:
    portfolio = await build_portfolio(db_path, cache)

    assert portfolio["positions"] == []
    assert portfolio["positions_value"] == 0.0
    assert portfolio["total_unrealized_pnl"] == 0.0
    assert portfolio["total_unrealized_pnl_pct"] == 0.0
    assert portfolio["total_value"] == pytest.approx(10_000.0)


async def test_positions_are_sorted_by_market_value_descending(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 1, "buy")  # 200
    await execute_trade(db_path, cache, "MSFT", 2, "buy")  # 800

    portfolio = await build_portfolio(db_path, cache)

    assert [item["ticker"] for item in portfolio["positions"]] == ["MSFT", "AAPL"]


async def test_compute_total_value_matches_the_portfolio_total(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")

    total = await compute_total_value(db_path, cache)

    assert total == pytest.approx((await build_portfolio(db_path, cache))["total_value"])


# ── History ──────────────────────────────────────────────────────────────


def test_downsample_preserves_the_range_endpoints() -> None:
    """Truncating instead would silently redraw the chart as a shorter
    window — same shape, same axis, different meaning."""
    rows = list(range(1000))

    thinned = downsample(rows, 10)

    assert len(thinned) == 10
    assert thinned[0] == 0
    assert thinned[-1] == 999


def test_downsample_returns_everything_when_under_the_limit() -> None:
    assert downsample([1, 2, 3], 500) == [1, 2, 3]


def test_downsample_to_one_point_reports_the_latest() -> None:
    assert downsample([1, 2, 3], 1) == [3]


async def test_history_rejects_out_of_range_limit(db_path: str) -> None:
    for limit in (0, -1, HISTORY_MAX_LIMIT + 1):
        with pytest.raises(HistoryError) as caught:
            await get_portfolio_history(db_path, limit=limit)
        assert caught.value.status_code == 400


async def test_history_rejects_unparseable_since(db_path: str) -> None:
    with pytest.raises(HistoryError) as caught:
        await get_portfolio_history(db_path, since="last tuesday")

    assert caught.value.status_code == 400


async def test_history_returns_snapshots_oldest_first(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 1, "buy")
    await execute_trade(db_path, cache, "AAPL", 1, "buy")

    history = await get_portfolio_history(db_path)

    assert len(history["points"]) == 2
    assert history["points"][0]["recorded_at"] <= history["points"][1]["recorded_at"]


async def test_history_downsamples_rather_than_truncating(
    db_path: str, cache: PriceCache
) -> None:
    """The regression db-engineer flagged: passing the request's `limit`
    into `list_snapshots` would return the *oldest* N rows, so the chart
    would show only the start of the window and look plausible doing it."""
    for _ in range(20):
        await execute_trade(db_path, cache, "AAPL", 0.001, "buy")

    everything = await get_portfolio_history(db_path)
    thinned = await get_portfolio_history(db_path, limit=5)

    assert len(thinned["points"]) == 5
    # The last point of the thinned series is the last point of the window,
    # not the fifth-oldest.
    assert thinned["points"][-1] == everything["points"][-1]
