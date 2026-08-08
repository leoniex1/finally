from __future__ import annotations

from app.config import Settings
from app.market_data.factory import build_market_data_provider
from app.market_data.massive_provider import MassiveProvider
from app.market_data.simulator_provider import SimulatorProvider


def test_no_api_key_selects_simulator() -> None:
    settings = Settings(massive_api_key="")
    provider = build_market_data_provider(settings)

    assert isinstance(provider, SimulatorProvider)


def test_api_key_selects_massive() -> None:
    settings = Settings(massive_api_key="secret", massive_poll_interval_seconds=5.0)
    provider = build_market_data_provider(settings)

    assert isinstance(provider, MassiveProvider)
    assert provider.poll_interval_seconds == 5.0


def test_settings_use_massive_property() -> None:
    assert Settings(massive_api_key="x").use_massive is True
    assert Settings(massive_api_key="").use_massive is False
