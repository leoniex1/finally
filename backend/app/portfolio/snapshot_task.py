"""The portfolio-snapshot background task (`API_CONTRACT.md` §5.1,
`PLAN.md` §7).

Every 30 seconds this records one `portfolio_snapshots` row, which is the
entire data source for the P&L chart. Trades also write a snapshot inline
(`service.execute_trade`), so the series carries both the steps trades
produce and the drift between them.

It also prunes. At a 30-second cadence this table grows ~2,880 rows a day and
the Docker volume persists indefinitely, so without pruning the only thing
bounding it is the disk. Retention is 30 days (`PLAN.md` §7).

The loop is deliberately split from its body: `run_snapshot_once` is a single
iteration with no sleeping and no exception handling, which is what the tests
drive. Nothing here catches its own exceptions — `run_supervised` owns
restart-with-backoff, and a task that swallowed its own errors would sit
there logging nothing while the P&L chart quietly flatlined.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..db.connection import connect, transaction
from ..db.init import DEFAULT_USER_ID
from ..db.snapshots_repo import insert_snapshot, prune_snapshots
from ..market_data.cache import PriceCache
from .service import compute_total_value

logger = logging.getLogger("app.portfolio.snapshot_task")

#: `PLAN.md` §7: "Recorded every 30 seconds by a background task". A module
#: constant so tests can shrink it rather than waiting on wall-clock time.
SNAPSHOT_INTERVAL_SECONDS = 30.0

#: `PLAN.md` §7 retention. Rows older than this are dropped on each run.
SNAPSHOT_RETENTION = timedelta(days=30)


async def run_snapshot_once(
    db_path: str, cache: PriceCache, user_id: str = DEFAULT_USER_ID
) -> float:
    """Record one snapshot and prune expired rows. Returns the value written.

    Valuation happens before the write and outside the transaction: it opens
    its own connection to read cash and positions, and holding a write
    transaction across that read would serialise the snapshot task against
    every trade for no benefit.
    """
    total_value = await compute_total_value(db_path, cache, user_id)
    cutoff = (datetime.now(timezone.utc) - SNAPSHOT_RETENTION).isoformat()

    async with connect(db_path) as db:
        async with transaction(db):
            await insert_snapshot(db, total_value, user_id)
            pruned = await prune_snapshots(db, cutoff, user_id)

    if pruned:
        logger.info("pruned %d portfolio snapshots older than %s", pruned, cutoff)

    return total_value


async def run_snapshot_loop(
    db_path: str,
    cache: PriceCache,
    user_id: str = DEFAULT_USER_ID,
    interval_seconds: float = SNAPSHOT_INTERVAL_SECONDS,
) -> None:
    """Record a snapshot every `interval_seconds`, forever.

    Sleeps *first*. The lifespan starts this immediately after `init_db`, and
    a snapshot taken at that moment would be recorded before the market data
    driver's first cycle has priced anything — every position unpriced, so
    every one valued at cost basis (`service._value_position`). That point is
    not wrong exactly, but it is a fabricated flat spot at the left edge of
    every P&L chart after every restart. One interval's delay costs nothing
    and lets the cache fill first.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        total_value = await run_snapshot_once(db_path, cache, user_id)
        logger.debug("recorded portfolio snapshot: %.2f", total_value)
