"""HTTP behaviour of the portfolio, watchlist and chat routes.

The service tests cover the rules; these cover the translation — status
codes, response shapes, and the `{"detail": ...}` envelope the frontend
renders verbatim (`API_CONTRACT.md` §2). The cases worth having here are the
ones where FastAPI's defaults would quietly disagree with the contract.
"""

from __future__ import annotations

import pytest

from app.market_data.cache import PriceCache


async def test_health_is_shallow_and_ok(app_client) -> None:
    response = await app_client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_get_portfolio_reports_the_seeded_balance(app_client) -> None:
    body = (await app_client.get("/api/portfolio")).json()

    assert body["cash_balance"] == pytest.approx(10_000.0)
    assert body["positions"] == []


async def test_buy_then_portfolio_reflects_the_position(app_client) -> None:
    trade = await app_client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    )
    assert trade.status_code == 200

    body = (await app_client.get("/api/portfolio")).json()
    assert body["cash_balance"] == pytest.approx(8_000.0)
    assert body["positions"][0]["ticker"] == "AAPL"


@pytest.mark.parametrize(
    ("payload", "status", "fragment"),
    [
        ({"ticker": "AAPL", "quantity": 0, "side": "buy"}, 400, "Invalid quantity"),
        ({"ticker": "AAPL", "quantity": "x", "side": "buy"}, 400, "Invalid quantity"),
        ({"ticker": "AAPL", "side": "buy"}, 400, "Invalid quantity"),
        ({"ticker": "AAPL", "quantity": 1, "side": "hold"}, 400, "Invalid side"),
        ({"ticker": "TOOLONG", "quantity": 1, "side": "buy"}, 400, "Invalid ticker"),
        ({"ticker": "ZZZZ", "quantity": 1, "side": "buy"}, 409, "No price available"),
        ({"ticker": "AAPL", "quantity": 9999, "side": "buy"}, 422, "Insufficient cash"),
        ({"ticker": "AAPL", "quantity": 1, "side": "sell"}, 422, "Insufficient shares"),
    ],
)
async def test_trade_rejections_use_the_contract_status_and_detail(
    app_client, payload, status, fragment
) -> None:
    """The reason `TradeRequest`'s fields are untyped.

    With Pydantic types, a missing or non-numeric quantity would surface as
    FastAPI's `422` in its own error envelope — a different status and a
    different body than §3.3 specifies, decided by whether the client sent a
    string or a number.
    """
    response = await app_client.post("/api/portfolio/trade", json=payload)

    assert response.status_code == status
    assert fragment in response.json()["detail"]


async def test_history_bounds_are_enforced(app_client) -> None:
    assert (await app_client.get("/api/portfolio/history?limit=0")).status_code == 400
    assert (await app_client.get("/api/portfolio/history?limit=99999")).status_code == 400
    assert (await app_client.get("/api/portfolio/history?since=nope")).status_code == 400
    assert (await app_client.get("/api/portfolio/history")).status_code == 200


async def test_watchlist_add_remove_round_trip(app_client) -> None:
    added = await app_client.post("/api/watchlist", json={"ticker": "pypl"})
    assert added.status_code == 200
    assert added.json()["ticker"] == "PYPL"

    # Idempotent: a second add is a 200, not a duplicate and not a conflict.
    assert (await app_client.post("/api/watchlist", json={"ticker": "PYPL"})).status_code == 200

    removed = await app_client.delete("/api/watchlist/PYPL")
    assert removed.status_code == 204
    # Idempotent in the other direction too (§3.7).
    assert (await app_client.delete("/api/watchlist/PYPL")).status_code == 204


async def test_watchlist_rejects_a_malformed_ticker(app_client) -> None:
    response = await app_client.post("/api/watchlist", json={"ticker": "TOOLONG"})

    assert response.status_code == 400
    assert "Invalid ticker" in response.json()["detail"]


async def test_watchlist_get_includes_market_data(app_client) -> None:
    body = (await app_client.get("/api/watchlist")).json()
    aapl = next(item for item in body["tickers"] if item["ticker"] == "AAPL")

    assert aapl["status"] == "ok"
    assert aapl["price"] == 200.0


# ── Chat ─────────────────────────────────────────────────────────────────


async def test_chat_mock_executes_a_valid_trade_and_reports_it(app_client) -> None:
    response = await app_client.post("/api/chat", json={"message": "buy 1 AAPL"})
    body = response.json()

    assert response.status_code == 200
    assert body["actions"][0]["status"] == "applied"
    assert body["actions"][0]["detail"]["fill_price"] == 200.0
    assert (await app_client.get("/api/portfolio")).json()["cash_balance"] == pytest.approx(9_800.0)


async def test_chat_failed_action_contradicts_the_prose(app_client) -> None:
    """The failure mode `PLAN.md` §9 is built around.

    The mock's message claims the buy is happening; $18k of NVDA will not fit
    in $10k of cash. Without the `actions` array the user sees a confident
    confirmation of a trade that never happened.
    """
    body = (await app_client.post("/api/chat", json={"message": "buy 100 NVDA"})).json()

    assert "Buying 100 NVDA" in body["message"]
    action = body["actions"][0]
    assert action["status"] == "failed"
    assert "Insufficient cash" in action["error"]
    # And the portfolio is genuinely untouched.
    assert (await app_client.get("/api/portfolio")).json()["cash_balance"] == pytest.approx(10_000.0)


async def test_chat_unpriced_ticker_fails_with_409_reason(app_client) -> None:
    body = (await app_client.post("/api/chat", json={"message": "buy zzzz"})).json()

    assert body["actions"][0]["status"] == "failed"
    assert "No price available" in body["actions"][0]["error"]


async def test_chat_watchlist_changes_apply(app_client) -> None:
    body = (await app_client.post("/api/chat", json={"message": "add PYPL"})).json()

    assert body["actions"][0]["kind"] == "watchlist_change"
    assert body["actions"][0]["status"] == "applied"
    tickers = [t["ticker"] for t in (await app_client.get("/api/watchlist")).json()["tickers"]]
    assert "PYPL" in tickers


async def test_chat_history_survives_and_carries_action_results(app_client) -> None:
    """The stored outcomes are how the model learns on the next turn that a
    trade failed — there is no second LLM call to tell it."""
    await app_client.post("/api/chat", json={"message": "buy 100 NVDA"})

    messages = (await app_client.get("/api/chat/history")).json()["messages"]

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["actions"] is None
    assert messages[1]["actions"][0]["status"] == "failed"


async def test_chat_rejects_an_empty_message(app_client) -> None:
    assert (await app_client.post("/api/chat", json={"message": "   "})).status_code == 400


async def test_chat_history_limit_is_bounded(app_client) -> None:
    assert (await app_client.get("/api/chat/history?limit=0")).status_code == 400
    assert (await app_client.get("/api/chat/history?limit=9999")).status_code == 400


async def test_chat_is_503_without_a_key_while_the_rest_of_the_app_works(
    db_path: str, cache: PriceCache
) -> None:
    """`PLAN.md` §5: a missing key disables chat only. It must not prevent
    startup or affect any other endpoint."""
    import httpx

    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(db_path=db_path, llm_mock=False, openrouter_api_key=""))
    app.state.price_cache = cache

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        chat = await client.post("/api/chat", json={"message": "hello"})
        assert chat.status_code == 503
        assert "OPENROUTER_API_KEY" in chat.json()["detail"]

        assert (await client.get("/api/portfolio")).status_code == 200
        assert (await client.get("/api/watchlist")).status_code == 200
        # History still serves: a transcript is worth showing even when new
        # messages cannot be sent.
        assert (await client.get("/api/chat/history")).status_code == 200
