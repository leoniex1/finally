"""Contract tests that must hold for *every* `MarketDataProvider`, run
against both shipped implementations (`MARKET_INTERFACE.md` §8).

The `aclose()` cases exist because the lifespan handler in `app/main.py`
shuts the active provider down with a provider-agnostic
`await provider.aclose()`. If that hook ever stops being part of the base
interface, Massive's HTTP connection pool leaks on shutdown — the exact
failure Finding #3 of `planning/MARKET_DATA_REVIEW.md` flagged.
"""

from __future__ import annotations

from typing import AbstractSet, Mapping

import pytest

from app.market_data.massive_provider import MassiveProvider
from app.market_data.provider import MarketDataProvider, Quote
from app.market_data.simulator_provider import SimulatorProvider


@pytest.fixture(params=["simulator", "massive"])
def provider(request) -> MarketDataProvider:
    if request.param == "simulator":
        return SimulatorProvider()
    return MassiveProvider(api_key="test-key")


def test_base_interface_declares_aclose() -> None:
    # A consumer only ever sees the ABC, so the hook has to live there —
    # not only on the one implementation that happens to need it.
    assert hasattr(MarketDataProvider, "aclose")


def test_aclose_is_not_abstract() -> None:
    # A no-op default is what keeps `SimulatorProvider` from having to
    # write an empty override just to stay instantiable.
    assert "aclose" not in getattr(MarketDataProvider, "__abstractmethods__", frozenset())


async def test_every_provider_can_be_closed(provider: MarketDataProvider) -> None:
    assert await provider.aclose() is None


async def test_aclose_is_idempotent(provider: MarketDataProvider) -> None:
    # Shutdown can race a supervisor restart; closing twice must not raise.
    await provider.aclose()
    await provider.aclose()


async def test_simulator_aclose_leaves_provider_usable() -> None:
    # The simulator owns no resource, so closing it is genuinely a no-op —
    # asserted rather than assumed, since the lifespan calls it blindly.
    provider = SimulatorProvider()
    await provider.aclose()

    quotes = await provider.fetch({"AAPL"})
    assert quotes["AAPL"].price > 0


async def test_massive_aclose_closes_the_http_client() -> None:
    provider = MassiveProvider(api_key="test-key")
    assert provider._client.is_closed is False

    await provider.aclose()

    assert provider._client.is_closed is True


async def test_a_custom_provider_inherits_aclose_without_overriding() -> None:
    """A third source added later gets correct shutdown for free — this is
    the property that makes an `isinstance` special-case unnecessary."""

    class ThirdPartyProvider(MarketDataProvider):
        poll_interval_seconds = 1.0

        async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
            return {}

    provider = ThirdPartyProvider()  # instantiable without writing aclose()
    assert await provider.aclose() is None


@pytest.mark.parametrize("cls", [SimulatorProvider, MassiveProvider])
def test_both_providers_expose_a_poll_interval(cls: type[MarketDataProvider]) -> None:
    provider = cls() if cls is SimulatorProvider else cls(api_key="k")  # type: ignore[call-arg]
    assert isinstance(provider.poll_interval_seconds, float)
    assert provider.poll_interval_seconds > 0
