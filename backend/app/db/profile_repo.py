"""Reads and writes for `users_profile` — the cash balance
(`API_CONTRACT.md` §4.2).

No function here commits. Wrap calls in `connection.transaction()`.
"""

from __future__ import annotations

import aiosqlite

from .init import DEFAULT_USER_ID


class ProfileMissingError(LookupError):
    """No `users_profile` row for this user.

    `init_db` seeds the profile before any request is served or any
    background task starts (`PLAN.md` §7), so this is a bug — a database that
    skipped initialization, or a test fixture that wrote rows without one.
    It is raised rather than defaulted because both silent answers are
    dangerous: reading `0.0` makes every buy look unaffordable, and writing
    into nothing discards the cash side of a trade whose `trades` row was
    already appended.
    """


async def get_cash_balance(
    db: aiosqlite.Connection, user_id: str = DEFAULT_USER_ID
) -> float:
    """The user's uninvested cash."""
    cursor = await db.execute(
        "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
    )
    record = await cursor.fetchone()
    if record is None:
        raise ProfileMissingError(f"no users_profile row for user_id={user_id!r}")
    return float(record["cash_balance"])


async def set_cash_balance(
    db: aiosqlite.Connection, value: float, user_id: str = DEFAULT_USER_ID
) -> None:
    """Overwrite the cash balance with an absolute figure.

    Absolute, not a delta: the trade service reads the balance and computes
    the new one inside the same transaction, so there is no lost-update
    window to protect against, and a caller can never accidentally apply the
    same delta twice.
    """
    cursor = await db.execute(
        "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (value, user_id)
    )
    if cursor.rowcount == 0:
        raise ProfileMissingError(f"no users_profile row for user_id={user_id!r}")
