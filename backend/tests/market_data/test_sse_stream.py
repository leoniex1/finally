"""`GET /api/stream/prices` — the `PLAN.md` §12 SSE bullets:
"unchanged tickers emit no events" and "a new connection receives a full
snapshot".

These drive `price_event_stream()` directly rather than through an HTTP
client (`market-data-design.md` §15): it is a plain async generator over a
`PriceCache`, so a background drain task plus cache mutations exercises the
`updated_at` gate exactly, with no server, no sockets, and no dependence on
a real 15-second Massive poll cadence.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app.api.routes.market_data import price_event_stream, stream_prices
from app.market_data.cache import PriceCache
from app.market_data.driver import run_market_data_cycle
from app.market_data.models import TickerStatus
from app.market_data.provider import MarketDataProvider, Quote

CHECK_INTERVAL = 0.01
#: Effectively "never" — heartbeat behavior gets its own dedicated test, so
#: everything else can count price events without a ping in the way.
NO_HEARTBEAT = 10_000.0


async def _drain(gen, sink: list[str]) -> None:
    async for chunk in gen:
        sink.append(chunk)


def _start(cache: PriceCache, **kwargs) -> tuple[asyncio.Task, list[str]]:
    sink: list[str] = []
    gen = price_event_stream(
        cache,
        kwargs.pop("is_disconnected", None),
        check_interval_seconds=kwargs.pop("check_interval_seconds", CHECK_INTERVAL),
        heartbeat_interval_seconds=kwargs.pop("heartbeat_interval_seconds", NO_HEARTBEAT),
    )
    return asyncio.create_task(_drain(gen, sink)), sink


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


async def _settle() -> None:
    """Give the stream several check cycles to emit anything it was going
    to emit, so a "nothing further was sent" assertion is meaningful."""
    await asyncio.sleep(CHECK_INTERVAL * 8)


def _events(sink: list[str]) -> list[dict]:
    return [json.loads(chunk.removeprefix("data: ")) for chunk in sink if chunk.startswith("data: ")]


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── Initial snapshot ─────────────────────────────────────────────────────


async def test_new_connection_receives_a_full_snapshot(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("MSFT", 420.0, now=100.0)
    price_cache.update("TSLA", 250.0, now=100.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 3)
        assert {e["ticker"] for e in _events(sink)} == {"AAPL", "MSFT", "TSLA"}
    finally:
        await _stop(task)


async def test_snapshot_includes_tickers_that_have_never_ticked(price_cache: PriceCache) -> None:
    # A pending ticker has updated_at == 0.0; the snapshot is unconditional,
    # so the client can render "—" for it immediately instead of a blank cell.
    price_cache.ensure_tracked("PENDING")

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)
        event = _events(sink)[0]
        assert event["ticker"] == "PENDING"
        assert event["status"] == TickerStatus.PENDING.value
        assert event["price"] is None
    finally:
        await _stop(task)


async def test_snapshot_carries_every_field_the_frontend_needs(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("AAPL", 191.0, now=101.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)
        event = _events(sink)[0]
        assert set(event) == {
            "ticker",
            "price",
            "prev_price",
            "reference_price",
            "reference_kind",
            "change_pct",
            "direction",
            "status",
            "updated_at",
        }
        assert event["direction"] == "up"
    finally:
        await _stop(task)


async def test_a_second_connection_gets_its_own_full_snapshot(price_cache: PriceCache) -> None:
    # `last_sent_at` is per-connection, so a reconnecting EventSource is
    # repopulated without any client-side catch-up logic.
    price_cache.update("AAPL", 190.0, now=100.0)

    first_task, first_sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(first_sink) >= 1)

        second_task, second_sink = _start(price_cache)
        try:
            assert await _wait_until(lambda: len(second_sink) >= 1)
            assert _events(second_sink)[0]["ticker"] == "AAPL"
        finally:
            await _stop(second_task)
    finally:
        await _stop(first_task)


# ── The updated_at gate ──────────────────────────────────────────────────


async def test_unchanged_tickers_emit_no_events(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)
        await _settle()  # many check cycles, no cache writes at all

        assert len(sink) == 1  # only the initial snapshot
    finally:
        await _stop(task)


async def test_a_price_change_emits_exactly_one_event(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)

        price_cache.update("AAPL", 191.0, now=101.0)
        assert await _wait_until(lambda: len(sink) >= 2)
        await _settle()

        assert len(sink) == 2
        assert _events(sink)[1]["price"] == 191.0
        assert _events(sink)[1]["direction"] == "up"
    finally:
        await _stop(task)


async def test_repeated_identical_price_emits_nothing_after_the_snapshot(
    price_cache: PriceCache,
) -> None:
    """The Finding #1 fix, observed from the stream's side.

    `PriceCache.update()` leaves `updated_at` alone on an exact repeat, so
    these writes are invisible here. Before that fix each one would have
    advanced `updated_at` and pushed a duplicate event with
    `prev_price == price` — a "flat" tick that suppresses the frontend's
    flash animation and pads the sparkline.
    """
    price_cache.update("CHEAP", 30.28, now=100.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)

        for i in range(1, 21):
            price_cache.update("CHEAP", 30.28, now=100.0 + i)
        await _settle()

        assert len(sink) == 1
    finally:
        await _stop(task)


async def test_massive_poll_cadence_does_not_produce_duplicate_events(
    price_cache: PriceCache, tracked_set_stub
) -> None:
    """The case `PLAN.md` §6 calls out by name: at a 15-second Massive poll
    a naive fixed-cadence stream would emit ~30 identical events per real
    tick. Driven end-to-end through the real driver here, with a provider
    that keeps answering with the same quote."""

    class StuckProvider(MarketDataProvider):
        poll_interval_seconds = 0.0

        async def fetch(self, tickers):
            return {t: Quote(ticker=t, price=190.0) for t in tickers}

    tracked_set_stub.set({"AAPL"})
    provider = StuckProvider()
    await run_market_data_cycle(provider, price_cache, tracked_set_stub)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)

        for _ in range(30):
            await run_market_data_cycle(provider, price_cache, tracked_set_stub)
        await _settle()

        assert len(sink) == 1
    finally:
        await _stop(task)


async def test_only_the_changed_ticker_is_re_emitted(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)
    price_cache.update("MSFT", 420.0, now=100.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 2)

        price_cache.update("MSFT", 421.0, now=101.0)
        assert await _wait_until(lambda: len(sink) >= 3)
        await _settle()

        assert len(sink) == 3
        assert _events(sink)[2]["ticker"] == "MSFT"
    finally:
        await _stop(task)


async def test_status_change_to_unavailable_is_not_emitted_until_updated_at_moves(
    price_cache: PriceCache,
) -> None:
    # `mark_unavailable` deliberately does not touch `updated_at` (it freezes
    # the last known price), so the stream stays quiet — documenting the
    # actual contract rather than an assumed one.
    price_cache.update("AAPL", 190.0, now=100.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)

        price_cache.mark_unavailable("AAPL")
        await _settle()
        assert len(sink) == 1

        # Recovering from unavailable IS a real observation, even at the
        # same price, so that one does get through.
        price_cache.update("AAPL", 190.0, now=105.0)
        assert await _wait_until(lambda: len(sink) >= 2)
        assert _events(sink)[1]["status"] == TickerStatus.OK.value
    finally:
        await _stop(task)


async def test_retracked_ticker_is_re_emitted_after_being_dropped(
    price_cache: PriceCache,
) -> None:
    """Bookkeeping for a dropped ticker is pruned, so a ticker that leaves
    the tracked set and comes back is treated as "never sent" — otherwise
    its stale, higher `updated_at` would suppress its new events forever."""
    price_cache.update("AAPL", 190.0, now=500.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)

        price_cache.drop_untracked(set())  # removed from the watchlist, no position
        await _settle()  # lets the prune branch run
        assert len(sink) == 1

        # Re-added, and re-seeded with an EARLIER timestamp than before.
        price_cache.update("AAPL", 188.0, now=100.0)
        assert await _wait_until(lambda: len(sink) >= 2)
        assert _events(sink)[1]["price"] == 188.0
    finally:
        await _stop(task)


# ── Heartbeat and disconnect ─────────────────────────────────────────────


async def test_heartbeat_is_a_comment_line(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    task, sink = _start(price_cache, heartbeat_interval_seconds=0.0)
    try:
        assert await _wait_until(lambda: any(chunk == ": ping\n\n" for chunk in sink))
    finally:
        await _stop(task)


async def test_no_heartbeat_before_its_interval_elapses(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    task, sink = _start(price_cache)  # 10,000s heartbeat interval
    try:
        assert await _wait_until(lambda: len(sink) >= 1)
        await _settle()

        assert not any(chunk.startswith(":") for chunk in sink)
    finally:
        await _stop(task)


async def test_stream_ends_when_the_client_disconnects(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    async def disconnected() -> bool:
        return True

    task, sink = _start(price_cache, is_disconnected=disconnected)

    # The generator returns rather than looping forever, so the drain task
    # completes on its own — no cancellation needed.
    await asyncio.wait_for(task, timeout=2.0)
    assert len(sink) == 1  # the snapshot was still delivered


async def test_stream_stays_open_while_connected(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    async def connected() -> bool:
        return False

    task, sink = _start(price_cache, is_disconnected=connected)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)
        await _settle()
        assert not task.done()
    finally:
        await _stop(task)


# ── Wire format ──────────────────────────────────────────────────────────


async def test_route_returns_a_streaming_response_with_sse_headers(
    price_cache: PriceCache,
) -> None:
    """The generator tests above bypass the route; this covers the wiring
    itself — the content type and proxy-buffering headers a real
    `EventSource` client depends on, and that the body really is the price
    stream reading `app.state.price_cache`.

    Driven against the handler rather than an HTTP client on purpose:
    `httpx.ASGITransport` runs the ASGI app to completion, and this
    response never completes by design.
    """
    price_cache.update("AAPL", 190.0, now=100.0)

    class StubApp:
        state = SimpleNamespace(price_cache=price_cache)

    class StubRequest:
        app = StubApp()

        async def is_disconnected(self) -> bool:
            return False

    response = await stream_prices(StubRequest())

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    body = response.body_iterator
    first = await asyncio.wait_for(body.__anext__(), timeout=2.0)
    assert json.loads(first.removeprefix("data: ").rstrip())["ticker"] == "AAPL"
    await body.aclose()


async def test_events_are_valid_sse_frames(price_cache: PriceCache) -> None:
    price_cache.update("AAPL", 190.0, now=100.0)

    task, sink = _start(price_cache)
    try:
        assert await _wait_until(lambda: len(sink) >= 1)
        frame = sink[0]
        assert frame.startswith("data: ")
        assert frame.endswith("\n\n")
        json.loads(frame.removeprefix("data: ").rstrip())  # one JSON object per frame
    finally:
        await _stop(task)
