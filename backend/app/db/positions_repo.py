"""Reads and writes for the `positions` table (`API_CONTRACT.md` §4.2).

A row exists only while a holding is open (`PLAN.md` §7). This layer stores
whatever quantity and average cost it is handed — the epsilon rule that
decides *when* a position becomes a delete instead of an upsert, and the
weighted-average-cost arithmetic, both live in the trade service, which is
the one place that knows a fill happened.

No function here commits. Wrap calls in `connection.transaction()`.
"""

from __future__ import annotations

import uuid

import aiosqlite

from .init import DEFAULT_USER_ID, utc_now_iso
from .rows import PositionRow

_COLUMNS = "id, user_id, ticker, quantity, avg_cost, updated_at"


def _row(record: aiosqlite.Row) -> PositionRow:
    return PositionRow(
        id=record["id"],
        user_id=record["user_id"],
        ticker=record["ticker"],
        quantity=record["quantity"],
        avg_cost=record["avg_cost"],
        updated_at=record["updated_at"],
    )


async def list_positions(
    db: aiosqlite.Connection, user_id: str = DEFAULT_USER_ID
) -> list[PositionRow]:
    """Every open position, ticker-ordered.

    Ticker order, not the `market_value` descending order of
    `API_CONTRACT.md` §3.2: market value needs the price cache, so the
    portfolio service sorts. Ordering here at all is so the sequence is
    stable across calls rather than SQLite's incidental row order.
    """
    cursor = await db.execute(
        f"SELECT {_COLUMNS} FROM positions WHERE user_id = ? ORDER BY ticker ASC",
        (user_id,),
    )
    return [_row(record) for record in await cursor.fetchall()]


async def get_position(
    db: aiosqlite.Connection, ticker: str, user_id: str = DEFAULT_USER_ID
) -> PositionRow | None:
    """The open position in `ticker`, or `None` when flat. `None` means "no
    holding", never "zero shares" — the sell path deletes the row rather than
    leaving it at zero."""
    cursor = await db.execute(
        f"SELECT {_COLUMNS} FROM positions WHERE user_id = ? AND ticker = ?",
        (user_id, ticker),
    )
    record = await cursor.fetchone()
    return _row(record) if record is not None else None


async def upsert_position(
    db: aiosqlite.Connection,
    ticker: str,
    quantity: float,
    avg_cost: float,
    user_id: str = DEFAULT_USER_ID,
) -> PositionRow:
    """
    Write the position's absolute state — `quantity` and `avg_cost` *replace*
    what is stored, they are not added to it. The caller has already done the
    weighted-average-cost math and knows the resulting holding.

    An existing row keeps its `id` (the row is updated in place, not replaced),
    so anything holding a position id across a trade stays valid.
    """
    now = utc_now_iso()
    await db.execute(
        """
        INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (user_id, ticker) DO UPDATE SET
            quantity   = excluded.quantity,
            avg_cost   = excluded.avg_cost,
            updated_at = excluded.updated_at
        """,
        (str(uuid.uuid4()), user_id, ticker, quantity, avg_cost, now),
    )
    position = await get_position(db, ticker, user_id)
    assert position is not None, f"position row for {ticker!r} disappeared mid-transaction"
    return position


async def delete_position(
    db: aiosqlite.Connection, ticker: str, user_id: str = DEFAULT_USER_ID
) -> None:
    """Close the position by removing its row.

    Deleting rather than zeroing is what makes the state binary: the
    positions table and the heatmap can never show a phantom 0-share holding,
    and the E2E "position disappears" assertion is deterministic
    (`PLAN.md` §7). Deleting an absent position is a no-op.
    """
    await db.execute(
        "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
    )
