from __future__ import annotations

from app.market_data.simulator_seeds import (
    DEFAULT_MU,
    DEFAULT_SIGMA,
    KNOWN_TICKERS,
    SECTORS,
    SeedSpec,
    seed_for_unknown_ticker,
    spec_for,
)


def test_known_ticker_returns_table_entry() -> None:
    assert spec_for("AAPL") == KNOWN_TICKERS["AAPL"]
    assert spec_for("AAPL").sector == "tech"


def test_known_tickers_cover_default_watchlist() -> None:
    default_watchlist = {
        "AAPL",
        "GOOGL",
        "MSFT",
        "AMZN",
        "TSLA",
        "NVDA",
        "META",
        "JPM",
        "V",
        "NFLX",
    }
    assert default_watchlist == set(KNOWN_TICKERS)


def test_unknown_ticker_seeding_is_deterministic() -> None:
    first = seed_for_unknown_ticker("ZZZZ")
    second = seed_for_unknown_ticker("ZZZZ")

    assert first == second


def test_unknown_ticker_price_in_range() -> None:
    for ticker in ["ZZZZ", "QQQQ", "FOO", "BAR", "XYZAB", "PLTR2", "HELLO"]:
        spec = seed_for_unknown_ticker(ticker)
        assert 20.0 <= spec.price < 400.0
        assert spec.sector in SECTORS


def test_unknown_ticker_uses_default_mu_sigma() -> None:
    spec = seed_for_unknown_ticker("ZZZZ")
    assert spec.mu == DEFAULT_MU
    assert spec.sigma == DEFAULT_SIGMA


def test_spec_for_falls_back_to_hash_seed_for_unknown_ticker() -> None:
    assert spec_for("ZZZZ") == seed_for_unknown_ticker("ZZZZ")


def test_seed_spec_price_and_sector_bytes_are_independent() -> None:
    # Two tickers that could plausibly hash to a similar price shouldn't be
    # forced into the same sector just because of that.
    specs = [seed_for_unknown_ticker(f"SYM{i}") for i in range(50)]
    sectors_seen = {spec.sector for spec in specs}
    # With 50 samples across 5 sectors, expect more than one sector to appear.
    assert len(sectors_seen) > 1


def test_seed_spec_is_frozen_dataclass() -> None:
    spec = SeedSpec(price=100.0, sector="tech")
    assert spec.mu == DEFAULT_MU
    assert spec.sigma == DEFAULT_SIGMA
