from __future__ import annotations

import math
import statistics

from app.market_data.simulator_engine import (
    EVENT_PROBABILITY,
    MIN_PRICE,
    TICK_INTERVAL_SECONDS,
    TRADING_SECONDS_PER_YEAR,
    SimulatorEngine,
)
from app.market_data.simulator_seeds import spec_for


def _run_steps(engine: SimulatorEngine, tickers: set[str], n: int) -> dict[str, list[float]]:
    series: dict[str, list[float]] = {t: [] for t in tickers}
    engine.sync_tracked(tickers)
    for t in tickers:
        series[t].append(engine._prices[t])  # seed price, for computing the first return
    for _ in range(n):
        prices = engine.step(tickers)
        for t in tickers:
            series[t].append(prices[t])
    return series


def _log_returns(prices: list[float]) -> list[float]:
    return [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]


def test_newly_tracked_ticker_prices_on_first_step() -> None:
    engine = SimulatorEngine(seed=1)
    engine.sync_tracked({"AAPL"})
    prices = engine.step({"AAPL"})

    assert set(prices) == {"AAPL"}
    assert isinstance(prices["AAPL"], float)
    assert prices["AAPL"] > 0


def test_step_returns_one_price_per_requested_ticker() -> None:
    engine = SimulatorEngine(seed=1)
    engine.sync_tracked({"AAPL", "MSFT", "JPM"})
    prices = engine.step({"AAPL", "MSFT", "JPM"})

    assert set(prices) == {"AAPL", "MSFT", "JPM"}


def test_untrack_and_retrack_resets_to_seed_price() -> None:
    engine = SimulatorEngine(seed=2)
    engine.sync_tracked({"AAPL"})
    for _ in range(50):
        engine.step({"AAPL"})

    assert engine._prices["AAPL"] != spec_for("AAPL").price  # drifted from the seed

    engine.sync_tracked(set())  # drop
    assert "AAPL" not in engine._prices

    engine.sync_tracked({"AAPL"})  # retrack
    assert engine._prices["AAPL"] == spec_for("AAPL").price


def test_price_never_crosses_floor_regardless_of_starting_point() -> None:
    engine = SimulatorEngine(seed=3)
    engine.sync_tracked({"AAPL"})
    # Force the internal price to an implausibly small value; the floor must
    # still hold on the very next step regardless of the computed GBM move.
    engine._prices["AAPL"] = 1e-9

    new_price = engine.step({"AAPL"})["AAPL"]

    assert new_price >= MIN_PRICE


def test_dt_matches_tick_interval_over_trading_year() -> None:
    dt = TICK_INTERVAL_SECONDS / TRADING_SECONDS_PER_YEAR
    assert dt == TICK_INTERVAL_SECONDS / (252 * 6.5 * 3600)
    assert 0 < dt < 1e-6


def test_gbm_drift_is_close_to_theoretical_mean_over_many_steps() -> None:
    engine = SimulatorEngine(seed=42)
    ticker = "AAPL"
    spec = spec_for(ticker)
    n = 20_000

    series = _run_steps(engine, {ticker}, n)
    returns = _log_returns(series[ticker])

    assert len(returns) == n
    assert all(p >= MIN_PRICE for p in series[ticker])
    # Not a degenerate constant walk.
    assert len({round(r, 10) for r in returns}) > 1

    dt = TICK_INTERVAL_SECONDS / TRADING_SECONDS_PER_YEAR
    theoretical_mean = (spec.mu - 0.5 * spec.sigma**2) * dt
    empirical_mean = statistics.fmean(returns)

    # Per-tick drift is astronomically small relative to diffusion noise at
    # this dt, so this is a coarse sanity band, not a tight estimate — it
    # would only fail if the drift/diffusion formula were structurally
    # broken (e.g. off by orders of magnitude, or drift dominating diffusion).
    assert abs(empirical_mean - theoretical_mean) < 1e-3


def test_event_jumps_fire_at_roughly_the_configured_rate() -> None:
    engine = SimulatorEngine(seed=7)
    ticker = "AAPL"
    n = 20_000

    series = _run_steps(engine, {ticker}, n)
    returns = _log_returns(series[ticker])

    # Pure diffusion at this dt essentially never produces a >1.5% move, so
    # this threshold is a reliable proxy for "an event jump fired that tick".
    jump_like = [r for r in returns if abs(r) > 0.015]
    observed_rate = len(jump_like) / n

    # Generous order-of-magnitude band around EVENT_PROBABILITY (0.002).
    assert EVENT_PROBABILITY / 10 <= observed_rate <= EVENT_PROBABILITY * 10


def test_same_sector_tickers_are_more_correlated_than_cross_sector() -> None:
    engine = SimulatorEngine(seed=123)
    tickers = {"AAPL", "GOOGL", "JPM"}  # AAPL/GOOGL are both "tech"; JPM is "finance"
    n = 5_000

    series = _run_steps(engine, tickers, n)
    returns = {t: _log_returns(series[t]) for t in tickers}

    same_sector_corr = statistics.correlation(returns["AAPL"], returns["GOOGL"])
    cross_sector_corr = statistics.correlation(returns["AAPL"], returns["JPM"])

    assert same_sector_corr > 0.3
    assert -0.2 <= cross_sector_corr <= 0.2
    assert same_sector_corr > cross_sector_corr + 0.15
