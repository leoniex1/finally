"""Reads and writes for `portfolio_snapshots` — the P&L chart's time series
(`API_CONTRACT.md` §4.2).

No function here commits. Wrap calls in `connection.transaction()`.
"""

from __future__ import annotations

import uuid

import aiosqlite

from .init import DEFAULT_USER_ID, utc_now_iso
from .rows import SnapshotRow

_COLUMNS = "id, user_id, total_value, recorded_at"


def _row(record: aiosqlite.Row) -> SnapshotRow:
    return SnapshotRow(
        id=record["id"],
        user_id=record["user_id"],
        total_value=record["total_value"],
        recorded_at=record["recorded_at"],
    )


async def insert_snapshot(
    db: aiosqlite.Connection, total_value: float, user_id: str = DEFAULT_USER_ID
) -> SnapshotRow:
    """Record one portfolio-value point.

    Written on a 30-second timer *and* immediately after every trade
    (`PLAN.md` §7), so the chart shows the step a fill produced instead of
    smoothing it away between two timer ticks.
    """
    snapshot = SnapshotRow(
        id=str(uuid.uuid4()),
        user_id=user_id,
        total_value=total_value,
        recorded_at=utc_now_iso(),
    )
    await db.execute(
        f"INSERT INTO portfolio_snapshots ({_COLUMNS}) VALUES (?, ?, ?, ?)",
        (snapshot.id, snapshot.user_id, snapshot.total_value, snapshot.recorded_at),
    )
    return snapshot


async def list_snapshots(
    db: aiosqlite.Connection,
    since_iso: str,
    limit: int,
    user_id: str = DEFAULT_USER_ID,
) -> list[SnapshotRow]:
    """
    Snapshots recorded at or after `since_iso`, oldest first, at most `limit`.

    `since_iso` is compared as a string, which is why every timestamp is
    written through `utc_now_iso()` in one fixed format — mixing offsets
    would make `>=` compare text that no longer matches chronological order.

    **`limit` here is a row cap, not the chart's point count.** It takes the
    *oldest* `limit` rows in the window, so passing the API's `limit` straight
    through would return the start of the window and nothing else, and the
    even downsampling §3.4 requires would have nothing left to downsample.
    Fetch with a cap comfortably above the requested point count (the
    contract's ceiling is 2,000 points, and 24h at 30s is ~2,880 rows), then
    downsample in the API layer — that split is deliberate (§4.2).
    """
    cursor = await db.execute(
        f"SELECT {_COLUMNS} FROM portfolio_snapshots"
        " WHERE user_id = ? AND recorded_at >= ?"
        " ORDER BY recorded_at ASC, rowid ASC LIMIT ?",
        (user_id, since_iso, limit),
    )
    return [_row(record) for record in await cursor.fetchall()]


async def prune_snapshots(
    db: aiosqlite.Connection, older_than_iso: str, user_id: str = DEFAULT_USER_ID
) -> int:
    """
    Delete snapshots recorded strictly before `older_than_iso`, returning the
    number of rows removed.

    At 30-second cadence this table grows ~2,880 rows a day into a Docker
    volume that persists indefinitely, so the snapshot loop prunes past the
    30-day retention window on every run (`PLAN.md` §7). Strictly before, so
    a row exactly on the boundary survives and the retained window is never
    short by one point.
    """
    cursor = await db.execute(
        "DELETE FROM portfolio_snapshots WHERE user_id = ? AND recorded_at < ?",
        (user_id, older_than_iso),
    )
    return cursor.rowcount
