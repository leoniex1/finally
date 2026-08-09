"""The snapshot background task (`API_CONTRACT.md` §5.1, `PLAN.md` §7)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.connection import connect, transaction
from app.db.snapshots_repo import insert_snapshot, list_snapshots
from app.market_data.cache import PriceCache
from app.market_data.supervisor import run_supervised
from app.portfolio.service import execute_trade
from app.portfolio.snapshot_task import (
    SNAPSHOT_RETENTION,
    run_snapshot_loop,
    run_snapshot_once,
)

EPOCH = "1970-01-01T00:00:00+00:00"


async def _snapshots(db_path: str) -> list:
    async with connect(db_path) as db:
        return await list_snapshots(db, EPOCH, 10_000)


async def test_one_iteration_writes_exactly_one_row(
    db_path: str, cache: PriceCache
) -> None:
    value = await run_snapshot_once(db_path, cache)

    rows = await _snapshots(db_path)
    assert len(rows) == 1
    assert rows[0].total_value == pytest.approx(10_000.0)
    assert value == pytest.approx(10_000.0)


async def test_snapshot_reflects_positions_at_market_value(
    db_path: str, cache: PriceCache
) -> None:
    await execute_trade(db_path, cache, "AAPL", 10, "buy")  # -$2,000 cash
    cache.update("AAPL", 250.0)  # position now worth $2,500

    await run_snapshot_once(db_path, cache)

    assert (await _snapshots(db_path))[-1].total_value == pytest.approx(10_500.0)


async def test_pruning_drops_expired_rows_and_keeps_current_ones(
    db_path: str, cache: PriceCache
) -> None:
    """Retention is 30 days (`PLAN.md` §7). At a 30-second cadence this table
    grows ~2,880 rows a day into a volume that persists indefinitely, so
    without pruning the only bound is the disk."""
    stale = (datetime.now(timezone.utc) - SNAPSHOT_RETENTION - timedelta(days=1)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    # `insert_snapshot` always stamps "now", which is the right API for
    # production and useless for testing retention — so these two rows go in
    # directly, the only place in the suite that writes SQL by hand.
    async with connect(db_path) as db:
        async with transaction(db):
            await db.executemany(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
                " VALUES (?, 'default', ?, ?)",
                [("stale-row", 1.0, stale), ("fresh-row", 2.0, fresh)],
            )

    await run_snapshot_once(db_path, cache)

    values = [row.total_value for row in await _snapshots(db_path)]
    assert 1.0 not in values
    assert 2.0 in values


async def test_loop_sleeps_before_its_first_snapshot(
    db_path: str, cache: PriceCache
) -> None:
    """Snapshotting immediately at startup would record every position at
    cost basis — the cache has not been filled by the driver yet — putting a
    fabricated flat spot at the left edge of the chart after every restart.
    """
    task = asyncio.create_task(run_snapshot_loop(db_path, cache, interval_seconds=5.0))
    await asyncio.sleep(0.05)
    task.cancel()

    assert await _snapshots(db_path) == []


async def test_loop_records_repeatedly(db_path: str, cache: PriceCache) -> None:
    task = asyncio.create_task(run_snapshot_loop(db_path, cache, interval_seconds=0.01))
    await asyncio.sleep(0.1)
    task.cancel()

    assert len(await _snapshots(db_path)) >= 2


async def test_supervisor_restarts_a_failing_iteration(
    db_path: str, cache: PriceCache
) -> None:
    """A raise must be logged and retried, not kill the task and leave the
    app running with a P&L chart that quietly stops advancing."""
    attempts = 0

    async def flaky() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient database error")
        await asyncio.sleep(3600)

    task = asyncio.create_task(
        run_supervised("snapshots_under_test", flaky, initial_backoff_seconds=0.01)
    )
    await asyncio.sleep(0.2)
    task.cancel()

    assert attempts >= 3
