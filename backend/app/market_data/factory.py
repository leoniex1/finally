"""Provider selection (`PLAN.md` §5, `MARKET_INTERFACE.md` §6): exactly one
switch, checked once at startup."""

from __future__ import annotations

from ..config import Settings
from .massive_provider import MassiveProvider
from .provider import MarketDataProvider
from .simulator_provider import SimulatorProvider


def build_market_data_provider(settings: Settings) -> MarketDataProvider:
    if settings.massive_api_key:
        return MassiveProvider(
            api_key=settings.massive_api_key,
            poll_interval_seconds=settings.massive_poll_interval_seconds,
        )
    return SimulatorProvider()
