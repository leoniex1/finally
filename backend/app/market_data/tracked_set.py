"""Computes the tracked set: `watchlist ∪ tickers with an open position`
(`PLAN.md` §6). Queried fresh on every driver cycle rather than pushed via
events — see the module docstring on `TrackedSetProvider` for the rationale.
"""

from __future__ import annotations

import aiosqlite


class TrackedSetProvider:
    """
    Computes tracked = watchlist tickers ∪ tickers with an open position.

    Queried fresh on every driver cycle (every ~500ms for the simulator,
    every poll interval for Massive) rather than pushed via events. Both
    tables are tiny (tens of rows), so a fresh SELECT per cycle is cheap and
    avoids a second consistency mechanism (pub/sub, invalidation) for what is
    already a polling loop. This also means there is exactly one source of
    truth (the DB) and zero risk of the in-memory tracked set drifting from
    it after a crash/restart mid-update.
    """

    def __init__(self, db_path: str, user_id: str = "default") -> None:
        self._db_path = db_path
        self._user_id = user_id

    async def get(self) -> set[str]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT ticker FROM watchlist WHERE user_id = ?
                UNION
                SELECT ticker FROM positions WHERE user_id = ? AND quantity > 1e-6
                """,
                (self._user_id, self._user_id),
            )
            rows = await cursor.fetchall()
        return {row[0] for row in rows}
