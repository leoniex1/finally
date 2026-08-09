"""`chat_repo` (`API_CONTRACT.md` §§3.8, 3.9, 4.2)."""

from __future__ import annotations

import aiosqlite
import pytest

from app.db import chat_repo
from app.db.connection import transaction

FAILED_TRADE = [
    {
        "kind": "trade",
        "status": "failed",
        "detail": {"ticker": "NVDA", "side": "buy", "quantity": 100},
        "error": "Insufficient cash: need $18,432.00, have $10,000.00",
    }
]


async def test_insert_returns_the_persisted_row(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        message = await chat_repo.insert_chat_message(db, "user", "buy 100 NVDA")

    assert (message.role, message.content) == ("user", "buy 100 NVDA")
    assert message.id and message.created_at
    assert await chat_repo.list_chat_messages(db) == [message]


async def test_actions_survive_a_json_round_trip(db: aiosqlite.Connection) -> None:
    """The failed-action record is what tells the model on the next turn that
    the trade it announced did not happen (`PLAN.md` §9)."""
    async with transaction(db):
        await chat_repo.insert_chat_message(
            db, "assistant", "Placing an order to buy 100 NVDA.", FAILED_TRADE
        )

    stored = (await chat_repo.list_chat_messages(db))[0]
    assert stored.actions == FAILED_TRADE
    assert stored.actions[0]["error"].startswith("Insufficient cash")


async def test_null_actions_round_trip_as_none(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await chat_repo.insert_chat_message(db, "user", "how am I doing?")

    assert (await chat_repo.list_chat_messages(db))[0].actions is None


async def test_empty_actions_stay_distinct_from_null(
    db: aiosqlite.Connection,
) -> None:
    """`[]` means "the model requested nothing"; `None` means "this message
    never had actions". §3.8 says `actions` is never `null` in a response, so
    the distinction has to survive storage."""
    async with transaction(db):
        await chat_repo.insert_chat_message(db, "assistant", "Analysis only.", [])

    stored = (await chat_repo.list_chat_messages(db))[0]
    assert stored.actions == []
    assert stored.actions is not None


async def test_stores_json_not_a_python_repr(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await chat_repo.insert_chat_message(db, "assistant", "…", FAILED_TRADE)

    cursor = await db.execute("SELECT actions FROM chat_messages")
    raw = (await cursor.fetchone())["actions"]
    assert raw.startswith("[{") and '"status": "failed"' in raw


async def test_lists_oldest_first(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        for content in ("first", "second", "third"):
            await chat_repo.insert_chat_message(db, "user", content)

    assert [m.content for m in await chat_repo.list_chat_messages(db)] == [
        "first",
        "second",
        "third",
    ]


async def test_the_limit_window_is_the_newest_messages(
    db: aiosqlite.Connection,
) -> None:
    """A long session must not permanently replay its opening messages while
    the model never sees what just happened."""
    async with transaction(db):
        for index in range(5):
            await chat_repo.insert_chat_message(db, "user", f"m{index}")

    recent = await chat_repo.list_chat_messages(db, limit=2)
    assert [m.content for m in recent] == ["m3", "m4"]


async def test_rejects_a_role_outside_the_enum(db: aiosqlite.Connection) -> None:
    with pytest.raises(aiosqlite.IntegrityError):
        async with transaction(db):
            await chat_repo.insert_chat_message(db, "system", "…")


async def test_scoped_to_the_user(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        await chat_repo.insert_chat_message(db, "user", "hi", user_id="other")

    assert await chat_repo.list_chat_messages(db) == []
    assert len(await chat_repo.list_chat_messages(db, user_id="other")) == 1


async def test_to_dict_is_a_history_message(db: aiosqlite.Connection) -> None:
    async with transaction(db):
        message = await chat_repo.insert_chat_message(
            db, "assistant", "Placing an order.", FAILED_TRADE
        )

    assert message.to_dict() == {
        "id": message.id,
        "role": "assistant",
        "content": "Placing an order.",
        "actions": FAILED_TRADE,
        "created_at": message.created_at,
    }
