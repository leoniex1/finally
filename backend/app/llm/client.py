"""The single LLM call (`PLAN.md` §9, `API_CONTRACT.md` §6).

One call per user message, structured output, validated into `LLMResponse`.
There is no second call and no streaming — Cerebras inference is fast enough
that a loading indicator covers the wait.

Provider routing (model, `extra_body`, `reasoning_effort`) comes from the
`cerebras` skill and pins inference to Cerebras via OpenRouter. Do not
re-derive those arguments from memory.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from ..config import Settings
from .mock import mock_response
from .prompt import build_messages
from .schemas import LLMResponse

logger = logging.getLogger("app.llm.client")

#: From the `cerebras` skill. `extra_body` is what actually pins the request
#: to the Cerebras provider — without it OpenRouter is free to route the model
#: to a slower backend, and the "fast enough that a spinner is fine" premise
#: of the no-streaming design (`PLAN.md` §9) stops holding.
DEFAULT_MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
REASONING_EFFORT = "low"


class LLMError(Exception):
    """Base for chat failures the route turns into an HTTP response."""

    status_code: int = 502

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class LLMUnavailableError(LLMError):
    """Chat is not configured. `/api/chat` maps this to `503` (§3.8).

    Deliberately not raised at import or startup: a missing
    `OPENROUTER_API_KEY` disables chat only, and the rest of the app must run
    normally (`PLAN.md` §5).
    """

    status_code = 503


class LLMUpstreamError(LLMError):
    """The provider failed, or returned something that is not a valid
    `LLMResponse`. `/api/chat` maps this to `502` (§3.8)."""

    status_code = 502


async def generate_response(
    user_message: str,
    *,
    portfolio: Mapping[str, Any],
    watchlist: Mapping[str, Any],
    history: Sequence[Any] = (),
    settings: Settings,
) -> LLMResponse:
    """Produce the model's structured response for one user message.

    Returns intent only. Nothing here executes a trade or touches the
    database — the caller runs the actions and builds the authoritative
    `actions` array (§6).

    Raises `LLMUnavailableError` when chat is not configured and
    `LLMUpstreamError` when the provider fails or its output does not
    validate.
    """
    if settings.llm_mock:
        # Mock mode replaces the call, not the pipeline: the caller still
        # executes these actions for real and reports real outcomes (§6.1).
        return mock_response(user_message)

    api_key = (settings.openrouter_api_key or "").strip()
    if not api_key:
        raise LLMUnavailableError(
            "Chat is unavailable: OPENROUTER_API_KEY is not configured"
        )

    messages = build_messages(
        user_message,
        portfolio=portfolio,
        watchlist=watchlist,
        history=history,
    )
    raw = await _call_llm(
        messages,
        model=settings.llm_model or DEFAULT_MODEL,
        api_key=api_key,
    )
    return parse_response(raw)


async def _call_llm(
    messages: list[dict[str, str]], *, model: str, api_key: str
) -> str:
    """Run the blocking LiteLLM call off the event loop.

    `litellm.completion` is synchronous and does real network I/O. Called
    directly it would stall the loop for the whole round trip, freezing the
    SSE price stream for every connected client while one user waits on chat.
    `to_thread` keeps the skill's call verbatim while getting it off the loop.

    The import is function-local so the rest of the app — and the entire mock
    path — does not pay LiteLLM's substantial import cost, and so unit tests
    can exercise everything around it without the package installed.
    """
    from litellm import completion  # noqa: PLC0415 - see docstring

    try:
        response = await asyncio.to_thread(
            completion,
            model=model,
            messages=messages,
            response_format=LLMResponse,
            reasoning_effort=REASONING_EFFORT,
            extra_body=EXTRA_BODY,
            api_key=api_key,
        )
        content = response.choices[0].message.content
    except Exception as exc:  # noqa: BLE001 - provider errors are open-ended
        logger.exception("LLM call failed")
        raise LLMUpstreamError(f"The AI assistant is unavailable: {exc}") from exc

    if not content:
        raise LLMUpstreamError("The AI assistant returned an empty response")
    return content


def parse_response(raw: str) -> LLMResponse:
    """Validate raw model output into an `LLMResponse`.

    Structured outputs make well-formed JSON the norm, not a guarantee: a
    provider fallback or a truncated generation still yields prose or a
    fenced block. Both surface as a `502` with a readable reason rather than
    a `ValidationError` escaping into a 500.
    """
    try:
        return LLMResponse.model_validate_json(_strip_code_fence(raw))
    except (ValidationError, ValueError) as exc:
        logger.warning("unparseable LLM response: %r", raw[:500])
        raise LLMUpstreamError(
            f"The AI assistant returned a malformed response: {exc}"
        ) from exc


def _strip_code_fence(raw: str) -> str:
    """Unwrap ```json … ``` if the model fenced its output anyway.

    The system prompt forbids fences, but a single stray fence turning a
    perfectly good response into a 502 is a bad trade for four lines of code.
    """
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body[:4].lower().startswith("json"):
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()


