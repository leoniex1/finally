"""Chat HTTP surface (`API_CONTRACT.md` §3.8, §3.9).

- `POST /api/chat`          — one message in, message + action results out
- `GET  /api/chat/history`  — prior conversation, so a reload keeps it

The order of operations in `post_chat` is the specification's, and it is
load-bearing (`PLAN.md` §9): context → history → one LLM call → execute →
persist → respond. In particular the assistant row is written *after*
execution, with the real outcomes attached, because those stored outcomes are
what tell the model on the next turn that a trade it announced never
happened. There is no second LLM call to discover that.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ...db.connection import connect, transaction
from ...db.chat_repo import insert_chat_message, list_chat_messages
from ...llm.client import LLMError, generate_response
from ...llm.executor import execute_actions
from ...market_data.cache import PriceCache
from ...portfolio.service import build_portfolio
from ...portfolio.watchlist_service import get_watchlist

router = APIRouter()

#: How much prior conversation is fed back to the model. Enough to keep a
#: multi-turn thread coherent — and, critically, to carry the ACTION RESULTS
#: of recent turns — without re-sending an unbounded transcript on every
#: message.
HISTORY_TURNS_FOR_PROMPT = 20

CHAT_HISTORY_DEFAULT_LIMIT = 50
CHAT_HISTORY_MAX_LIMIT = 200


class ChatRequest(BaseModel):
    message: str


@router.post("/api/chat")
async def post_chat(payload: ChatRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    cache: PriceCache = request.app.state.price_cache
    db_path = settings.db_path

    if not settings.chat_enabled:
        # `503`, not `500`: chat being unconfigured is a deployment choice,
        # and `PLAN.md` §5 requires the rest of the app to work normally.
        raise HTTPException(
            status_code=503,
            detail="Chat is unavailable: OPENROUTER_API_KEY is not configured",
        )

    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    portfolio = await build_portfolio(db_path, cache)
    watchlist = await get_watchlist(db_path, cache)

    async with connect(db_path) as db:
        history = await list_chat_messages(db, HISTORY_TURNS_FOR_PROMPT)

    try:
        response = await generate_response(
            message,
            portfolio=portfolio,
            watchlist=watchlist,
            history=history,
            settings=settings,
        )
    except LLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    # Execution happens before either row is written, so a crash mid-execution
    # cannot leave a stored assistant message claiming outcomes that were
    # never attempted.
    actions = await execute_actions(db_path, cache, response)

    async with connect(db_path) as db:
        async with transaction(db):
            await insert_chat_message(db, "user", message, None)
            assistant_row = await insert_chat_message(
                db, "assistant", response.message, actions
            )

    return {
        "message": assistant_row.content,
        "actions": actions,
        "created_at": assistant_row.created_at,
    }


@router.get("/api/chat/history")
async def get_chat_history(
    request: Request, limit: Optional[int] = Query(default=None)
) -> dict[str, Any]:
    """Prior conversation, oldest first (§3.9).

    Deliberately available even when chat is disabled: an existing transcript
    is still worth showing, and a `503` here would blank the panel on reload
    for a deployment that merely lost its API key.
    """
    resolved = CHAT_HISTORY_DEFAULT_LIMIT if limit is None else limit
    if resolved < 1 or resolved > CHAT_HISTORY_MAX_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid limit: must be between 1 and {CHAT_HISTORY_MAX_LIMIT}",
        )

    async with connect(request.app.state.settings.db_path) as db:
        rows = await list_chat_messages(db, resolved)

    return {"messages": [row.to_dict() for row in rows]}
