"""`snapshots_repo` (`API_CONTRACT.md` §§3.4, 4.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from app.db import snapshots_repo
from app.db.connection import transaction

CUTOFF = datetime(2026, 8, 8, tzinfo=timezone.utc)


async def _write(db: aiosqlite.Connection, *offsets_days: float, user_id: str = "default") -> None:
    for index, days in enumerate(offsets_days):
        recorded_at = (CUTOFF + timedelta(days=days)).isoformat()
        await db.execute(
            "INSERT INTO portfolio_snapshots VALUES (?,?,?,?)",
            (f"{user_id}-{index}", user_id, 10000.0 + index, recorded_at),
        )


async def test_insert_returns_the_persisted_row(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        snapshot = await snapshots_repo.insert_snapshot(db, 10412.33)

    assert snapshot.total_value == 10412.33
    assert snapshot.id and snapshot.recorded_at

    stored = await snapshots_repo.list_snapshots(db, "1970-01-01T00:00:00+00:00", 10)
    assert stored == [snapshot]


async def test_lists_oldest_first(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await _write(db, 2, 0, 1)

    rows = await snapshots_repo.list_snapshots(db, "1970-01-01T00:00:00+00:00", 10)
    assert [row.recorded_at for row in rows] == sorted(row.recorded_at for row in rows)


async def test_since_is_inclusive_and_excludes_earlier_rows(
    db: aiosqlite.Connection,
) -> None:
    async with transaction(db):
        await _write(db, -1, 0, 1)

    rows = await snapshots_repo.list_snapshots(db, CUTOFF.isoformat(), 10)
    assert len(rows) == 2
    assert all(row.recorded_at >= CUTOFF.isoformat() for row in rows)


async def test_limit_caps_the_rows_returned(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await _write(db, *range(10))

    rows = await snapshots_repo.list_snapshots(db, "1970-01-01T00:00:00+00:00", 3)
    assert len(rows) == 3


async def test_limit_takes_the_oldest_rows_in_the_window(
    db: aiosqlite.Connection,
) -> None:
    """Pinning the documented sharp edge: `limit` is a row cap, so the API
    layer must fetch above its display count and downsample (§3.4) rather
    than pass its own `limit` straight through."""
    async with transaction(db):
        await _write(db, 0, 1, 2, 3, 4)

    rows = await snapshots_repo.list_snapshots(db, "1970-01-01T00:00:00+00:00", 2)
    assert [row.total_value for row in rows] == [10000.0, 10001.0]


async def test_prune_deletes_only_rows_older_than_the_cutoff(
    db: aiosqlite.Connection,
) -> None:
    async with transaction(db):
        await _write(db, -40, -31, -29, 0)
        deleted = await snapshots_repo.prune_snapshots(
            db, (CUTOFF - timedelta(days=30)).isoformat()
        )

    assert deleted == 2
    remaining = await snapshots_repo.list_snapshots(db, "1970-01-01T00:00:00+00:00", 100)
    assert len(remaining) == 2


async def test_prune_keeps_a_row_exactly_on_the_boundary(
    db: aiosqlite.Connection,
) -> None:
    """Strictly-before, so the retained window is never short by one point."""
    async with transaction(db):
        await _write(db, 0)
        deleted = await snapshots_repo.prune_snapshots(db, CUTOFF.isoformat())

    assert deleted == 0


async def test_prune_reports_zero_when_nothing_matches(
    db: aiosqlite.Connection,
) -> None:
    async with transaction(db):
        assert await snapshots_repo.prune_snapshots(db, CUTOFF.isoformat()) == 0


async def test_scoped_to_the_user(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await _write(db, 0, user_id="other")
        await snapshots_repo.insert_snapshot(db, 1.0)

    mine = await snapshots_repo.list_snapshots(db, "1970-01-01T00:00:00+00:00", 100)
    assert [row.total_value for row in mine] == [1.0]

    async with transaction(db):
        assert await snapshots_repo.prune_snapshots(db, "2100-01-01T00:00:00+00:00") == 1

    theirs = await snapshots_repo.list_snapshots(
        db, "1970-01-01T00:00:00+00:00", 100, user_id="other"
    )
    assert len(theirs) == 1


async def test_to_dict_is_a_history_point(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        snapshot = await snapshots_repo.insert_snapshot(db, 10412.33)

    assert snapshot.to_dict() == {
        "recorded_at": snapshot.recorded_at,
        "total_value": 10412.33,
    }
