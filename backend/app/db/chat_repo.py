"""Reads and writes for `chat_messages` (`API_CONTRACT.md` §4.2).

The `actions` column is the only place in the schema holding JSON. Encoding
and decoding both happen here, so no caller ever sees a JSON string: the
route serializes a Python list into its response, and the next turn's prompt
builder reads back a Python list. A second `json.loads` somewhere upstream is
exactly how a stored `null` turns into the string `"null"`.

No function here commits. Wrap calls in `connection.transaction()`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import aiosqlite

from .init import DEFAULT_USER_ID, utc_now_iso
from .rows import ChatRow

_COLUMNS = "id, user_id, role, content, actions, created_at"


def _row(record: aiosqlite.Row) -> ChatRow:
    stored = record["actions"]
    return ChatRow(
        id=record["id"],
        user_id=record["user_id"],
        role=record["role"],
        content=record["content"],
        actions=json.loads(stored) if stored is not None else None,
        created_at=record["created_at"],
    )


async def insert_chat_message(
    db: aiosqlite.Connection,
    role: str,
    content: str,
    actions: Optional[list[Any]] = None,
    user_id: str = DEFAULT_USER_ID,
) -> ChatRow:
    """
    Append one message and return it.

    `actions` is a Python object (the §3.8 results array) or `None`; it is
    JSON-encoded here. SQL `NULL` and the empty list stay distinct: `None`
    means "this message never had actions" (every user message), while `[]`
    means "the model requested nothing this turn". The prompt builder reads
    both back unchanged, which is how the model learns on the following turn
    that a trade it announced was rejected.

    `role` must be `"user"` or `"assistant"` — the schema's CHECK constraint
    enforces it.
    """
    message = ChatRow(
        id=str(uuid.uuid4()),
        user_id=user_id,
        role=role,
        content=content,
        actions=actions,
        created_at=utc_now_iso(),
    )
    await db.execute(
        f"INSERT INTO chat_messages ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
        (
            message.id,
            message.user_id,
            message.role,
            message.content,
            json.dumps(actions) if actions is not None else None,
            message.created_at,
        ),
    )
    return message


async def list_chat_messages(
    db: aiosqlite.Connection, limit: int = 50, user_id: str = DEFAULT_USER_ID
) -> list[ChatRow]:
    """
    The `limit` most recent messages, returned oldest first.

    The window is taken from the *newest* end and then reversed, rather than
    reading the oldest `limit` rows: both the chat panel and the prompt's
    conversation history want the tail of the conversation, and a long
    session would otherwise permanently replay its opening messages while the
    model never saw what just happened.
    """
    cursor = await db.execute(
        f"SELECT {_COLUMNS} FROM chat_messages WHERE user_id = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (user_id, limit),
    )
    records = await cursor.fetchall()
    return [_row(record) for record in reversed(records)]
