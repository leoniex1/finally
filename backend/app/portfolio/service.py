"""Portfolio valuation and the single trade code path (`API_CONTRACT.md`
§3.2, §3.3, §3.4, §5).

`execute_trade` is the highest-stakes function in the backend and the only
place a fill is ever written. Both entry points reach it — the trade bar's
REST route and the LLM action executor (`PLAN.md` §9) — so the validation
ladder below is written once and shared, never re-implemented per caller.

Two invariants everything here is arranged around:

1. **A rejected trade writes nothing.** Every check that can fail runs
   before the first write, and the writes that do happen are inside one
   `transaction()`, so a raise anywhere rolls the whole thing back.
2. **A fill never happens at a price the cache did not actually observe.**
   Rule 4 of the §3.3 ladder refuses `None`, and refuses a `pending` or
   `unavailable` status. A trade written at `0.0` corrupts `avg_cost` and
   the cash balance permanently, and nothing downstream can detect it
   afterwards.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence

from ..db.connection import connect, transaction
from ..db.init import DEFAULT_USER_ID
from ..db.positions_repo import (
    delete_position,
    get_position,
    list_positions,
    upsert_position,
)
from ..db.profile_repo import get_cash_balance, set_cash_balance
from ..db.rows import PositionRow, SnapshotRow
from ..db.snapshots_repo import insert_snapshot, list_snapshots
from ..db.trades_repo import insert_trade
from ..market_data.cache import PriceCache
from ..market_data.models import TickerStatus
from .errors import HistoryError, ServiceError, TradeError  # noqa: F401  (re-export)
from .validation import (
    QUANTITY_EPSILON,
    normalize_ticker,
    validate_quantity,
    validate_side,
)

logger = logging.getLogger("app.portfolio.service")

#: `GET /api/portfolio/history` bounds (`API_CONTRACT.md` §3.4).
HISTORY_DEFAULT_LIMIT = 500
HISTORY_MAX_LIMIT = 2000
HISTORY_DEFAULT_WINDOW = timedelta(hours=24)

#: How many rows the history endpoint pulls out of SQLite before
#: downsampling. `list_snapshots` applies `LIMIT` in SQL and returns the
#: *oldest* rows in the window, so passing the caller's `limit` straight
#: through would return the first 500 points of a 24-hour window and call it
#: a day — a P&L chart showing only the start of the range, looking entirely
#: plausible while doing it. This cap sits far above any real window (24h at
#: 30s is ~2,880 rows; the 30-day retention ceiling is ~86,400) so the fetch
#: covers the whole range and the downsampler has something to thin.
HISTORY_FETCH_CAP = 100_000


# ── Trade execution ──────────────────────────────────────────────────────


async def execute_trade(
    db_path: str,
    cache: PriceCache,
    ticker: Any,
    quantity: Any,
    side: Any,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """
    Run the §3.3 validation ladder, then fill atomically.

    Raises `TradeError` on any rejection; returns the §3.3 success body.
    Ladder order is normative and evaluated top to bottom — quantity, side,
    ticker, price availability, then funds — so a request that is wrong in
    two ways reports the first reason, the same one every time.
    """
    resolved_quantity = _reject_400(validate_quantity, quantity)
    resolved_side = _reject_400(validate_side, side)
    symbol = _reject_400(normalize_ticker, ticker)
    price = _require_price(cache, symbol)

    async with connect(db_path) as db:
        async with transaction(db):
            cash_balance = await get_cash_balance(db, user_id)
            position = await get_position(db, symbol, user_id)

            if resolved_side == "buy":
                fill_quantity, new_position, new_cash = _plan_buy(
                    position, resolved_quantity, price, cash_balance
                )
            else:
                fill_quantity, new_position, new_cash = _plan_sell(
                    position, resolved_quantity, price, cash_balance
                )

            # Nothing above this line has written anything: every rejection
            # path has already raised, so the four writes below are the
            # atomic unit §3.3 requires.
            trade = await insert_trade(
                db, symbol, resolved_side, fill_quantity, price, user_id
            )
            if new_position is None:
                await delete_position(db, symbol, user_id)
                position_row = None
            else:
                position_row = await upsert_position(
                    db, symbol, new_position[0], new_position[1], user_id
                )
            await set_cash_balance(db, new_cash, user_id)

            # Snapshot inside the same transaction, after the position and
            # cash writes, so the P&L chart shows the step this fill produced
            # rather than smoothing it away until the next 30-second tick.
            # The read below sees this transaction's uncommitted writes
            # because it is the same connection.
            positions = await list_positions(db, user_id)
            await insert_snapshot(
                db, new_cash + _positions_value(positions, cache), user_id
            )

    return {
        "trade": trade.to_dict(),
        "cash_balance": new_cash,
        "position": position_row.to_dict() if position_row is not None else None,
    }


def _plan_buy(
    position: Optional[PositionRow],
    quantity: float,
    price: float,
    cash_balance: float,
) -> tuple[float, Optional[tuple[float, float]], float]:
    """Returns `(fill_quantity, (new_quantity, new_avg_cost), new_cash)`.

    Weighted average cost per §3.3: the new average blends the existing
    holding with this fill. A buy can never close a position, so the middle
    element is never `None` here.
    """
    cost = quantity * price
    if cost > cash_balance:
        raise TradeError(
            422,
            f"Insufficient cash: need {_money(cost)}, have {_money(cash_balance)}",
        )

    held = position.quantity if position is not None else 0.0
    held_cost = held * position.avg_cost if position is not None else 0.0
    new_quantity = held + quantity
    new_avg_cost = (held_cost + cost) / new_quantity
    return quantity, (new_quantity, new_avg_cost), cash_balance - cost


def _plan_sell(
    position: Optional[PositionRow],
    quantity: float,
    price: float,
    cash_balance: float,
) -> tuple[float, Optional[tuple[float, float]], float]:
    """Returns `(fill_quantity, (new_quantity, new_avg_cost) | None, new_cash)`.

    `None` for the position means "delete the row" — the epsilon rule from
    `PLAN.md` §7. Note the two roles the epsilon plays here, and that they
    are not the same thing:

    - As a *tolerance*, it lets a sell of `10.000000001` against a holding of
      `10` through, rather than failing on a float representation artefact.
    - As a *closing threshold*, it turns a sell that would leave less than
      `1e-6` behind into a full liquidation: the fill quantity is raised to
      exactly the held amount so the cash and the trade log account for the
      residue, and the row is deleted rather than left holding `2e-13`.

    A sell genuinely beyond the holding is rejected, never clamped — that
    would silently hand the user cash for shares they do not have.
    """
    held = position.quantity if position is not None else 0.0
    if position is None or quantity > held + QUANTITY_EPSILON:
        raise TradeError(
            422,
            f"Insufficient shares: tried to sell {_shares(quantity)},"
            f" hold {_shares(held)}",
        )

    if held - quantity < QUANTITY_EPSILON:
        # Full liquidation, residue included.
        return held, None, cash_balance + held * price

    remaining = held - quantity
    # `avg_cost` is unchanged by a sell: it records what the *remaining*
    # shares cost, and selling some of them does not change what the rest
    # were bought for. Realized P&L lives in the trades log.
    return quantity, (remaining, position.avg_cost), cash_balance + quantity * price


def _require_price(cache: PriceCache, ticker: str) -> float:
    """Rule 4 of the ladder — the one that is easy to miss and expensive to
    get wrong (`PLAN.md` §8).

    The trade bar takes free text and the LLM can name anything, so a symbol
    added moments ago (before its first tick) or one the data source does not
    recognise reaches here with no usable price. Without this refusal the
    handler either raises mid-write or fills at `0.0`, granting free shares
    and corrupting both `avg_cost` and the cash balance.
    """
    entry = cache.get(ticker)
    if entry is None or entry.status is not TickerStatus.OK or entry.price is None:
        raise TradeError(409, f"No price available for {ticker}")
    return entry.price


def _reject_400(validator: Callable[[Any], Any], raw: Any) -> Any:
    """Run a `validation.py` helper, converting its `ValueError` into a 400.

    The helper's message *is* the contract's `detail` string, so this never
    rewrites it — two copies of "Invalid quantity: ..." would drift.
    """
    try:
        return validator(raw)
    except ValueError as exc:
        raise TradeError(400, str(exc)) from exc


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _shares(quantity: float) -> str:
    """Share counts without trailing zeros — "10" not "10.000000", since
    fractional shares are supported but almost never used."""
    return f"{quantity:g}"


# ── Valuation ────────────────────────────────────────────────────────────


async def build_portfolio(
    db_path: str, cache: PriceCache, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    """The `GET /api/portfolio` body (`API_CONTRACT.md` §3.2)."""
    async with connect(db_path) as db:
        cash_balance = await get_cash_balance(db, user_id)
        positions = await list_positions(db, user_id)

    valued = [_value_position(position, cache) for position in positions]
    # `float(...)` on every total: `sum()` of an empty sequence is the int
    # `0`, and §3.2 pins the empty portfolio at `0.0`.
    positions_value = float(sum(item["market_value"] for item in valued))
    total_value = cash_balance + positions_value

    for item in valued:
        # Weight is a share of the *whole* portfolio, cash included, so the
        # heatmap tiles of a mostly-cash portfolio stay honestly small.
        item["weight"] = item["market_value"] / total_value if total_value else 0.0

    priced = [item for item in valued if item["priced"]]
    # `float(...)` for the same reason as `positions_value` above: `sum()` of
    # an empty sequence is the int `0`, and §3.2 pins every total on an empty
    # portfolio at `0.0`.
    total_pnl = float(sum(item["unrealized_pnl"] for item in priced))
    priced_cost_basis = float(sum(item["cost_basis"] for item in priced))

    valued.sort(key=lambda item: item["market_value"], reverse=True)

    return {
        "cash_balance": cash_balance,
        "total_value": total_value,
        "positions_value": positions_value,
        "total_unrealized_pnl": total_pnl,
        # Return on what is actually invested and priced, matching the
        # per-position `unrealized_pnl_pct` denominator. Unpriced positions
        # are excluded from both sides rather than being valued at cost,
        # which would report a guaranteed 0% on a position nobody can price.
        "total_unrealized_pnl_pct": (
            total_pnl / priced_cost_basis if priced_cost_basis else 0.0
        ),
        "positions": valued,
    }


async def compute_total_value(
    db_path: str, cache: PriceCache, user_id: str = DEFAULT_USER_ID
) -> float:
    """Cash plus the market value of every open position — one number, for
    the snapshot task (§5.1). Cheaper than `build_portfolio`: no per-position
    P&L, no weights, no sort."""
    async with connect(db_path) as db:
        cash_balance = await get_cash_balance(db, user_id)
        positions = await list_positions(db, user_id)
    return cash_balance + _positions_value(positions, cache)


def _value_position(position: PositionRow, cache: PriceCache) -> dict[str, Any]:
    """One element of `positions` in §3.2.

    An unpriced position falls back to its cost basis for market value and
    reports `priced: false` with null price and P&L. That combination is
    deliberate: the portfolio total stays a sane number, while the UI has an
    unambiguous flag telling it to render `—` instead of a P&L that would be
    exactly `0.00` and look like a real observation.
    """
    entry = cache.get(position.ticker)
    current_price: Optional[float] = (
        entry.price if entry is not None and entry.status is TickerStatus.OK else None
    )
    priced = current_price is not None
    cost_basis = position.quantity * position.avg_cost

    if current_price is not None:
        market_value = position.quantity * current_price
        unrealized_pnl: Optional[float] = market_value - cost_basis
        unrealized_pnl_pct: Optional[float] = (
            unrealized_pnl / cost_basis if cost_basis else None
        )
    else:
        market_value = cost_basis
        unrealized_pnl = None
        unrealized_pnl_pct = None

    return {
        "ticker": position.ticker,
        "quantity": position.quantity,
        "avg_cost": position.avg_cost,
        "current_price": current_price,
        "market_value": market_value,
        "cost_basis": cost_basis,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "weight": 0.0,  # filled in by the caller, which knows the total
        "priced": priced,
        "updated_at": position.updated_at,
    }


def _positions_value(positions: Sequence[PositionRow], cache: PriceCache) -> float:
    """Market value of a set of positions, unpriced ones at cost basis."""
    return sum(_value_position(position, cache)["market_value"] for position in positions)


# ── History ──────────────────────────────────────────────────────────────


async def get_portfolio_history(
    db_path: str,
    since: Optional[str] = None,
    limit: Optional[int] = None,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """The `GET /api/portfolio/history` body (`API_CONTRACT.md` §3.4).

    Raises `HistoryError` (400) on an unparseable `since` or an out-of-range
    `limit`.
    """
    resolved_limit = _resolve_limit(limit)
    since_iso = _resolve_since(since)

    async with connect(db_path) as db:
        rows = await list_snapshots(db, since_iso, HISTORY_FETCH_CAP, user_id)

    if len(rows) >= HISTORY_FETCH_CAP:
        # Only reachable if pruning stopped (the snapshot task died and the
        # supervisor could not restart it). Worth a log line, because the
        # visible symptom — a chart that ends early — looks like a data
        # problem rather than a background-task problem.
        logger.warning(
            "history fetch hit the %d-row cap; snapshots may not be pruning",
            HISTORY_FETCH_CAP,
        )

    return {
        "since": since_iso,
        "limit": resolved_limit,
        "points": [row.to_dict() for row in downsample(rows, resolved_limit)],
    }


def downsample(rows: Sequence[SnapshotRow], limit: int) -> list[SnapshotRow]:
    """Thin `rows` to at most `limit` points, evenly across the range.

    Truncating instead would silently redraw the chart as a shorter window —
    the same shape, the same axis, a different meaning. Even spacing keeps
    the first and last row, so the range endpoints the caller asked for are
    exactly the ones plotted.
    """
    count = len(rows)
    if count <= limit:
        return list(rows)
    if limit == 1:
        # No pair of endpoints to preserve; the latest value is the one a
        # single-point summary should report.
        return [rows[-1]]

    step = (count - 1) / (limit - 1)
    return [rows[round(index * step)] for index in range(limit)]


def _resolve_limit(limit: Optional[int]) -> int:
    if limit is None:
        return HISTORY_DEFAULT_LIMIT
    if limit < 1 or limit > HISTORY_MAX_LIMIT:
        raise HistoryError(
            400, f"Invalid limit: must be between 1 and {HISTORY_MAX_LIMIT}"
        )
    return limit


def _resolve_since(since: Optional[str]) -> str:
    """Normalize `since` to the exact ISO-8601 UTC form the database stores.

    The comparison in SQL is a string comparison (`snapshots_repo`), so a
    `since` carrying a different offset — `+02:00`, or none at all — would
    compare as text against `+00:00` timestamps and silently select the
    wrong window. Converting to UTC here is what keeps `>=` chronological.
    """
    if since is None:
        return (datetime.now(timezone.utc) - HISTORY_DEFAULT_WINDOW).isoformat()

    try:
        parsed = datetime.fromisoformat(since)
    except ValueError as exc:
        raise HistoryError(400, f"Invalid since: {since!r} is not an ISO-8601 timestamp") from exc

    if parsed.tzinfo is None:
        # A naive timestamp is read as UTC rather than as local time: the
        # server's timezone is an accident of deployment, and every stored
        # timestamp is UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
