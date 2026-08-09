"""Reads and writes for the append-only `trades` log (`API_CONTRACT.md` §4.2).

Append-only: there is no update and no delete. The log is the audit trail
for how cash and positions got where they are, so a correction is another
trade, never an edit.

No function here commits. Wrap calls in `connection.transaction()`.
"""

from __future__ import annotations

import uuid

import aiosqlite

from .init import DEFAULT_USER_ID, utc_now_iso
from .rows import TradeRow

_COLUMNS = "id, user_id, ticker, side, quantity, price, executed_at"


def _row(record: aiosqlite.Row) -> TradeRow:
    return TradeRow(
        id=record["id"],
        user_id=record["user_id"],
        ticker=record["ticker"],
        side=record["side"],
        quantity=record["quantity"],
        price=record["price"],
        executed_at=record["executed_at"],
    )


async def insert_trade(
    db: aiosqlite.Connection,
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    user_id: str = DEFAULT_USER_ID,
) -> TradeRow:
    """
    Append one fill and return it.

    `side` must be `"buy"` or `"sell"` — the schema's CHECK constraint
    enforces it, so an out-of-enum value from a mis-parsed LLM response
    raises `IntegrityError` here instead of being logged as a trade nobody
    can interpret. `price` is the cached fill price; the caller has already
    established it is real (`API_CONTRACT.md` §3.3, rule 4), because a trade
    written at `0.0` corrupts cash and average cost permanently.
    """
    trade = TradeRow(
        id=str(uuid.uuid4()),
        user_id=user_id,
        ticker=ticker,
        side=side,
        quantity=quantity,
        price=price,
        executed_at=utc_now_iso(),
    )
    await db.execute(
        f"INSERT INTO trades ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            trade.id,
            trade.user_id,
            trade.ticker,
            trade.side,
            trade.quantity,
            trade.price,
            trade.executed_at,
        ),
    )
    return trade


async def list_trades(
    db: aiosqlite.Connection, limit: int = 100, user_id: str = DEFAULT_USER_ID
) -> list[TradeRow]:
    """The most recent `limit` fills, newest first.

    `rowid` breaks ties on `executed_at`: several fills inside one chat turn
    can share a timestamp to the microsecond, and insertion order is the only
    thing that then distinguishes them.
    """
    cursor = await db.execute(
        f"SELECT {_COLUMNS} FROM trades WHERE user_id = ?"
        " ORDER BY executed_at DESC, rowid DESC LIMIT ?",
        (user_id, limit),
    )
    return [_row(record) for record in await cursor.fetchall()]
