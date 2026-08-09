"""The LLM call and its failure modes (`API_CONTRACT.md` §3.8, §6).

**No test here reaches the network.** Mock mode covers the happy path, and
the real path is exercised against a stub module injected into `sys.modules`
— which works precisely because `client._call_llm` imports `litellm` inside
the function.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.config import Settings
from app.llm.client import (
    DEFAULT_MODEL,
    EXTRA_BODY,
    LLMUnavailableError,
    LLMUpstreamError,
    generate_response,
    parse_response,
)
from app.llm.schemas import LLMResponse

PORTFOLIO: dict[str, Any] = {
    "cash_balance": 10000.0,
    "total_value": 10000.0,
    "positions_value": 0.0,
    "total_unrealized_pnl": 0.0,
    "total_unrealized_pnl_pct": 0.0,
    "positions": [],
}
WATCHLIST: dict[str, Any] = {"tickers": []}


def settings(**overrides: Any) -> Settings:
    """The real `Settings` (§5.2), constructed directly rather than from the
    environment so a developer's `.env` cannot leak a live API key into a
    test run."""
    return Settings(**overrides)


def install_stub_litellm(monkeypatch: pytest.MonkeyPatch, completion: Any) -> None:
    module = types.ModuleType("litellm")
    module.completion = completion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", module)


def llm_reply(content: str) -> Any:
    message = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


async def test_mock_mode_returns_the_mock_without_an_api_key() -> None:
    response = await generate_response(
        "buy 1 AAPL",
        portfolio=PORTFOLIO,
        watchlist=WATCHLIST,
        settings=settings(llm_mock=True),
    )

    assert response.trades[0].ticker == "AAPL"


async def test_missing_api_key_raises_the_503_error() -> None:
    with pytest.raises(LLMUnavailableError) as excinfo:
        await generate_response(
            "hello",
            portfolio=PORTFOLIO,
            watchlist=WATCHLIST,
            settings=settings(),
        )

    # The route renders `detail` verbatim; §3.8 fixes this exact string.
    assert excinfo.value.status_code == 503
    assert (
        excinfo.value.detail
        == "Chat is unavailable: OPENROUTER_API_KEY is not configured"
    )


async def test_whitespace_only_api_key_counts_as_missing() -> None:
    with pytest.raises(LLMUnavailableError):
        await generate_response(
            "hello",
            portfolio=PORTFOLIO,
            watchlist=WATCHLIST,
            settings=settings(openrouter_api_key="   "),
        )


async def test_successful_call_pins_inference_to_cerebras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return llm_reply(
            '{"message": "Placing an order to buy 1 AAPL.",'
            ' "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1}]}'
        )

    install_stub_litellm(monkeypatch, fake_completion)

    response = await generate_response(
        "buy me an apple share",
        portfolio=PORTFOLIO,
        watchlist=WATCHLIST,
        settings=settings(openrouter_api_key="sk-test"),
    )

    assert response.trades[0].quantity == 1.0
    assert captured["model"] == DEFAULT_MODEL
    assert captured["extra_body"] == EXTRA_BODY
    assert captured["response_format"] is LLMResponse
    assert captured["api_key"] == "sk-test"
    # System prompt, portfolio context, then the user's message.
    assert captured["messages"][-1] == {
        "role": "user",
        "content": "buy me an apple share",
    }
    assert "$10,000.00" in captured["messages"][1]["content"]


async def test_upstream_exception_becomes_a_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**_: Any) -> Any:
        raise RuntimeError("connection reset")

    install_stub_litellm(monkeypatch, boom)

    with pytest.raises(LLMUpstreamError) as excinfo:
        await generate_response(
            "hello",
            portfolio=PORTFOLIO,
            watchlist=WATCHLIST,
            settings=settings(openrouter_api_key="sk-test"),
        )

    assert excinfo.value.status_code == 502
    assert "connection reset" in excinfo.value.detail


async def test_empty_completion_becomes_a_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_stub_litellm(monkeypatch, lambda **_: llm_reply(""))

    with pytest.raises(LLMUpstreamError):
        await generate_response(
            "hello",
            portfolio=PORTFOLIO,
            watchlist=WATCHLIST,
            settings=settings(openrouter_api_key="sk-test"),
        )


async def test_unparseable_output_becomes_a_502_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_stub_litellm(
        monkeypatch, lambda **_: llm_reply("Sure! Here's what I think...")
    )

    with pytest.raises(LLMUpstreamError) as excinfo:
        await generate_response(
            "hello",
            portfolio=PORTFOLIO,
            watchlist=WATCHLIST,
            settings=settings(openrouter_api_key="sk-test"),
        )

    assert "malformed" in excinfo.value.detail


def test_parse_response_accepts_plain_json() -> None:
    response = parse_response('{"message": "hi"}')

    assert response.message == "hi"
    assert response.trades == []


def test_parse_response_unwraps_a_fenced_block() -> None:
    response = parse_response('```json\n{"message": "hi"}\n```')

    assert response.message == "hi"


def test_parse_response_unwraps_an_unlabelled_fence() -> None:
    assert parse_response('```\n{"message": "hi"}\n```').message == "hi"


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "{",
        '{"trades": []}',  # message is required
        '{"message": "x", "trades": [{"ticker": "AAPL", "side": "hold", "quantity": 1}]}',
        '{"message": "x", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 0}]}',
        '{"message": "x", "watchlist_changes": [{"ticker": "PYPL", "action": "delete"}]}',
    ],
)
def test_parse_response_rejects_invalid_output(raw: str) -> None:
    with pytest.raises(LLMUpstreamError):
        parse_response(raw)


async def test_a_custom_llm_model_is_honoured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return llm_reply('{"message": "ok"}')

    install_stub_litellm(monkeypatch, fake_completion)

    await generate_response(
        "hello",
        portfolio=PORTFOLIO,
        watchlist=WATCHLIST,
        settings=settings(openrouter_api_key="sk-test", llm_model="openrouter/other"),
    )

    assert captured["model"] == "openrouter/other"
