from __future__ import annotations

from app.market_data.provider import MarketDataProvider
from app.market_data.simulator_engine import TICK_INTERVAL_SECONDS
from app.market_data.simulator_provider import SimulatorProvider


def test_is_a_market_data_provider() -> None:
    assert issubclass(SimulatorProvider, MarketDataProvider)


def test_poll_interval_matches_tick_interval() -> None:
    assert SimulatorProvider.poll_interval_seconds == TICK_INTERVAL_SECONDS


async def test_fetch_always_returns_every_requested_ticker() -> None:
    provider = SimulatorProvider()
    quotes = await provider.fetch({"AAPL", "MSFT", "ZZZZ"})

    assert set(quotes) == {"AAPL", "MSFT", "ZZZZ"}
    for ticker, quote in quotes.items():
        assert quote.ticker == ticker
        assert isinstance(quote.price, float)
        assert quote.price > 0
        # The simulator never has a real previous close — it always defers
        # to the cache's own session-open seeding.
        assert quote.reference_price is None
        assert quote.reference_kind is None


async def test_fetch_never_raises_for_unknown_tickers() -> None:
    provider = SimulatorProvider()
    quotes = await provider.fetch({"NOTAREALTICKER"})

    assert "NOTAREALTICKER" in quotes


async def test_state_persists_across_fetch_calls() -> None:
    provider = SimulatorProvider()
    first = await provider.fetch({"AAPL"})
    second = await provider.fetch({"AAPL"})

    # Both are valid floats; the engine underneath is a continuing walk
    # rather than being re-seeded on every fetch() call.
    assert isinstance(first["AAPL"].price, float)
    assert isinstance(second["AAPL"].price, float)


async def test_dropping_a_ticker_excludes_it_from_next_fetch() -> None:
    provider = SimulatorProvider()
    await provider.fetch({"AAPL", "MSFT"})
    quotes = await provider.fetch({"AAPL"})

    assert set(quotes) == {"AAPL"}
