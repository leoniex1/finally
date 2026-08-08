from __future__ import annotations

from app.market_data.cache import PriceCache
from app.market_data.models import HISTORY_MAXLEN, ReferenceKind, TickerStatus


def test_ensure_tracked_creates_pending_entry(price_cache: PriceCache) -> None:
    entry = price_cache.ensure_tracked("AAPL")

    assert entry.ticker == "AAPL"
    assert entry.status == TickerStatus.PENDING
    assert entry.price is None


def test_ensure_tracked_is_idempotent(price_cache: PriceCache) -> None:
    first = price_cache.ensure_tracked("AAPL")
    second = price_cache.ensure_tracked("AAPL")

    assert first is second


def test_first_update_seeds_session_open_reference(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.price == 190.0
    assert entry.prev_price == 190.0  # first tick: prev == price
    assert entry.reference_price == 190.0
    assert entry.reference_kind == ReferenceKind.SESSION_OPEN
    assert entry.status == TickerStatus.OK
    assert entry.updated_at == 100.0
    assert len(entry.history) == 1
    assert entry.history[0].price == 190.0


def test_second_update_without_reference_keeps_existing_reference(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("AAPL", 191.5, now=101.0)

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.price == 191.5
    assert entry.prev_price == 190.0
    assert entry.reference_price == 190.0  # unchanged
    assert entry.reference_kind == ReferenceKind.SESSION_OPEN  # unchanged
    assert entry.updated_at == 101.0
    assert len(entry.history) == 2


def test_update_with_explicit_reference_sets_prev_close(price_cache: PriceCache) -> None:
    price_cache.update(
        "AAPL",
        190.12,
        reference_price=188.5,
        reference_kind=ReferenceKind.PREV_CLOSE,
        now=100.0,
    )

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.reference_price == 188.5
    assert entry.reference_kind == ReferenceKind.PREV_CLOSE


def test_update_with_reference_price_but_no_kind_defaults_to_prev_close(
    price_cache: PriceCache,
) -> None:
    price_cache.update("AAPL", 190.12, reference_price=188.5, now=100.0)

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.reference_kind == ReferenceKind.PREV_CLOSE


def test_repeated_identical_price_does_not_advance_updated_at(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("AAPL", 190.0, now=101.0)  # exact repeat, later tick

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.updated_at == 100.0  # unchanged by the no-op repeat


def test_repeated_identical_price_does_not_append_duplicate_history(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("AAPL", 190.0, now=101.0)
    price_cache.update("AAPL", 190.0, now=102.0)

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert len(entry.history) == 1
    assert entry.history[0].t == 100.0


def test_repeated_identical_price_keeps_status_ok(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("AAPL", 190.0, now=101.0)

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.status == TickerStatus.OK
    assert entry.price == 190.0


def test_real_change_after_repeats_advances_updated_at_and_appends_history(
    price_cache: PriceCache,
) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("AAPL", 190.0, now=101.0)  # no-op repeat
    price_cache.update("AAPL", 191.0, now=102.0)  # real change

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.updated_at == 102.0
    assert len(entry.history) == 2
    assert entry.prev_price == 190.0
    assert entry.direction == "up"


def test_repeated_price_still_allows_reference_update(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)  # seeds session-open reference at 190.0
    price_cache.update(
        "AAPL",
        190.0,
        reference_price=188.5,
        reference_kind=ReferenceKind.PREV_CLOSE,
        now=101.0,
    )

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.reference_price == 188.5
    assert entry.reference_kind == ReferenceKind.PREV_CLOSE
    assert entry.updated_at == 100.0  # still a no-op for the tick itself


def test_recovering_from_unavailable_with_same_price_advances_updated_at(
    price_cache: PriceCache,
) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.mark_unavailable("AAPL")
    price_cache.update("AAPL", 190.0, now=200.0)  # same price, but ticker is back

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.status == TickerStatus.OK
    assert entry.updated_at == 200.0  # status recovery is a real observation
    assert len(entry.history) == 2


def test_drop_untracked_removes_missing_tickers(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=1.0)
    price_cache.update("MSFT", 420.0, now=1.0)

    price_cache.drop_untracked({"AAPL"})

    assert price_cache.get("AAPL") is not None
    assert price_cache.get("MSFT") is None


def test_mark_unavailable_preserves_last_known_price(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=1.0)
    price_cache.mark_unavailable("AAPL")

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert entry.status == TickerStatus.UNAVAILABLE
    assert entry.price == 190.0  # frozen, not wiped


def test_mark_unavailable_on_never_ticked_ticker(price_cache: PriceCache) -> None:
    price_cache.mark_unavailable("ZZZZ")

    entry = price_cache.get("ZZZZ")
    assert entry is not None
    assert entry.status == TickerStatus.UNAVAILABLE
    assert entry.price is None


def test_history_capped_at_maxlen(price_cache: PriceCache) -> None:
    for i in range(HISTORY_MAXLEN + 500):
        price_cache.update("AAPL", 190.0 + i, now=float(i))

    entry = price_cache.get("AAPL")
    assert entry is not None
    assert len(entry.history) == HISTORY_MAXLEN
    # Oldest points evicted: the buffer holds the most recent MAXLEN ticks.
    assert entry.history[0].price == 190.0 + 500
    assert entry.history[-1].price == 190.0 + HISTORY_MAXLEN + 499


def test_direction_property(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=1.0)
    assert price_cache.get("AAPL").direction == "flat"  # first tick, prev == price

    price_cache.update("AAPL", 191.0, now=2.0)
    assert price_cache.get("AAPL").direction == "up"

    price_cache.update("AAPL", 190.5, now=3.0)
    assert price_cache.get("AAPL").direction == "down"

    price_cache.update("AAPL", 190.5, now=4.0)
    assert price_cache.get("AAPL").direction == "flat"


def test_change_pct_property(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 200.0, reference_price=100.0, now=1.0)
    assert price_cache.get("AAPL").change_pct == 1.0


def test_change_pct_none_without_price_or_reference(price_cache: PriceCache) -> None:
    entry = price_cache.ensure_tracked("AAPL")
    assert entry.change_pct is None


def test_snapshot_and_tracked_tickers(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=1.0)
    price_cache.update("MSFT", 420.0, now=1.0)

    assert price_cache.tracked_tickers() == {"AAPL", "MSFT"}
    assert {entry.ticker for entry in price_cache.snapshot()} == {"AAPL", "MSFT"}


def test_to_sse_event_shape(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    event = price_cache.get("AAPL").to_sse_event()

    assert event == {
        "ticker": "AAPL",
        "price": 190.0,
        "prev_price": 190.0,
        "reference_price": 190.0,
        "reference_kind": "session_open",
        "change_pct": 0.0,
        "direction": "flat",
        "status": "ok",
        "updated_at": 100.0,
    }
