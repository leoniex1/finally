"""`profile_repo` (`API_CONTRACT.md` §4.2)."""

from __future__ import annotations

import aiosqlite
import pytest

from app.db import profile_repo
from app.db.connection import transaction
from app.db.init import DEFAULT_CASH_BALANCE
from app.db.profile_repo import ProfileMissingError


async def test_reads_the_seeded_balance(db: aiosqlite.Connection) -> None:
    assert await profile_repo.get_cash_balance(db) == DEFAULT_CASH_BALANCE


async def test_set_then_get_round_trips(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await profile_repo.set_cash_balance(db, 6216.70)

    assert await profile_repo.get_cash_balance(db) == 6216.70


async def test_get_raises_when_the_profile_is_missing(
    db: aiosqlite.Connection,
) -> None:
    """Defaulting to `0.0` would make every buy look unaffordable, which is a
    far more confusing failure than a loud one."""
    async with transaction(db):
        await db.execute("DELETE FROM users_profile")

    with pytest.raises(ProfileMissingError):
        await profile_repo.get_cash_balance(db)


async def test_set_raises_when_the_profile_is_missing(
    db: aiosqlite.Connection,
) -> None:
    """A silent zero-row UPDATE would discard the cash side of a trade whose
    `trades` row was already appended."""
    async with transaction(db):
        await db.execute("DELETE FROM users_profile")

    with pytest.raises(ProfileMissingError):
        await profile_repo.set_cash_balance(db, 1.0)


async def test_scoped_to_the_user(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await db.execute(
            "INSERT INTO users_profile VALUES ('other', 55.0, '2026-01-01T00:00:00+00:00')"
        )
        await profile_repo.set_cash_balance(db, 1.0, user_id="other")

    assert await profile_repo.get_cash_balance(db) == DEFAULT_CASH_BALANCE
    assert await profile_repo.get_cash_balance(db, user_id="other") == 1.0
