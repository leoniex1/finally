from __future__ import annotations

import httpx
import pytest
import respx

from app.market_data.massive_provider import (
    MASSIVE_BASE_URL,
    SNAPSHOT_PATH,
    MassiveProvider,
)
from app.market_data.models import ReferenceKind
from app.market_data.provider import MarketDataProvider


def test_is_a_market_data_provider() -> None:
    assert issubclass(MassiveProvider, MarketDataProvider)


def test_default_poll_interval_is_fifteen_seconds() -> None:
    provider = MassiveProvider(api_key="test-key")
    assert provider.poll_interval_seconds == 15.0


def test_poll_interval_is_configurable() -> None:
    provider = MassiveProvider(api_key="test-key", poll_interval_seconds=2.0)
    assert provider.poll_interval_seconds == 2.0


async def test_fetch_with_no_tickers_returns_empty_without_a_request() -> None:
    provider = MassiveProvider(api_key="test-key")

    async def _boom(*args, **kwargs):
        raise AssertionError("fetch() must not make an HTTP call for an empty ticker set")

    provider._client.get = _boom  # type: ignore[method-assign]

    quotes = await provider.fetch(set())

    assert quotes == {}
    await provider.aclose()


@respx.mock
async def test_fetch_parses_snapshot_response() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "tickers": [
                    {
                        "ticker": "AAPL",
                        "lastTrade": {"p": 190.12},
                        "prevDay": {"c": 188.5},
                    },
                ],
            },
        )
    )
    provider = MassiveProvider(api_key="test-key")

    quotes = await provider.fetch({"AAPL", "TSLA"})  # TSLA absent from the mocked response

    assert quotes["AAPL"].ticker == "AAPL"
    assert quotes["AAPL"].price == 190.12
    assert quotes["AAPL"].reference_price == 188.5
    assert quotes["AAPL"].reference_kind == ReferenceKind.PREV_CLOSE
    assert "TSLA" not in quotes  # the unavailable case, asserted at the provider boundary

    await provider.aclose()


@respx.mock
async def test_fetch_without_prev_day_omits_reference() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "tickers": [
                    {"ticker": "BCAT", "lastTrade": {"p": 20.506}},
                ],
            },
        )
    )
    provider = MassiveProvider(api_key="test-key")

    quotes = await provider.fetch({"BCAT"})

    assert quotes["BCAT"].price == 20.506
    assert quotes["BCAT"].reference_price is None
    assert quotes["BCAT"].reference_kind is None

    await provider.aclose()


@respx.mock
async def test_fetch_skips_malformed_rows() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "tickers": [
                    {"ticker": "AAPL"},  # no lastTrade at all
                    {"lastTrade": {"p": 100.0}},  # no ticker symbol
                    {"ticker": "MSFT", "lastTrade": {"p": 420.0}},
                ],
            },
        )
    )
    provider = MassiveProvider(api_key="test-key")

    quotes = await provider.fetch({"AAPL", "MSFT"})

    assert set(quotes) == {"MSFT"}
    assert quotes["MSFT"].price == 420.0

    await provider.aclose()


@respx.mock
async def test_fetch_skips_row_with_non_numeric_price() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "tickers": [
                    {"ticker": "AAPL", "lastTrade": {"p": "not-a-number"}},
                    {"ticker": "MSFT", "lastTrade": {"p": 420.0}},
                ],
            },
        )
    )
    provider = MassiveProvider(api_key="test-key")

    # A malformed individual field must not raise out of fetch() — it's
    # exactly the "malformed row" case fetch() is contracted to swallow,
    # not a whole-request failure.
    quotes = await provider.fetch({"AAPL", "MSFT"})

    assert set(quotes) == {"MSFT"}

    await provider.aclose()


@respx.mock
async def test_fetch_skips_row_with_non_positive_price() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "tickers": [
                    {"ticker": "AAPL", "lastTrade": {"p": 0.0}},
                    {"ticker": "TSLA", "lastTrade": {"p": -5.0}},
                    {"ticker": "MSFT", "lastTrade": {"p": 420.0}},
                ],
            },
        )
    )
    provider = MassiveProvider(api_key="test-key")

    # A zero or negative price must never reach the cache — PLAN.md §8's
    # "free shares" failure mode requires trades never fill at price <= 0.
    quotes = await provider.fetch({"AAPL", "TSLA", "MSFT"})

    assert set(quotes) == {"MSFT"}

    await provider.aclose()


@respx.mock
async def test_fetch_skips_row_with_non_finite_price() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(
            200,
            content=b'{"status": "OK", "tickers": '
            b'[{"ticker": "AAPL", "lastTrade": {"p": NaN}}]}',
            headers={"Content-Type": "application/json"},
        )
    )
    provider = MassiveProvider(api_key="test-key")

    quotes = await provider.fetch({"AAPL"})

    assert quotes == {}

    await provider.aclose()


@respx.mock
async def test_fetch_omits_reference_for_malformed_prev_close_but_keeps_price() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "tickers": [
                    {"ticker": "AAPL", "lastTrade": {"p": 190.12}, "prevDay": {"c": "n/a"}},
                ],
            },
        )
    )
    provider = MassiveProvider(api_key="test-key")

    quotes = await provider.fetch({"AAPL"})

    # The price is still usable even though prevDay.c is garbage — the
    # provider just omits the reference and lets the cache's own
    # session-open seeding take over, rather than dropping the whole ticker.
    assert quotes["AAPL"].price == 190.12
    assert quotes["AAPL"].reference_price is None
    assert quotes["AAPL"].reference_kind is None

    await provider.aclose()


@respx.mock
async def test_fetch_propagates_whole_request_failures() -> None:
    respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(500, json={"status": "ERROR"})
    )
    provider = MassiveProvider(api_key="test-key")

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch({"AAPL"})

    await provider.aclose()


@respx.mock
async def test_fetch_sends_bearer_auth_header_and_sorted_tickers() -> None:
    route = respx.get(f"{MASSIVE_BASE_URL}{SNAPSHOT_PATH}").mock(
        return_value=httpx.Response(200, json={"status": "OK", "tickers": []})
    )
    provider = MassiveProvider(api_key="secret-key")

    await provider.fetch({"MSFT", "AAPL"})

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert request.url.params["tickers"] == "AAPL,MSFT"  # sorted, comma-joined

    await provider.aclose()
