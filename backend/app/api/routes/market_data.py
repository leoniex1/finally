"""Market data HTTP surface (`PLAN.md` §8, `market-data-design.md` §12–§13):

- `GET /api/stream/prices`            — SSE stream of live price updates
- `GET /api/prices/{ticker}/history`  — the cache's in-memory ring buffer

Both read the shared `PriceCache` off `app.state.price_cache`, which the
lifespan handler in `app/main.py` puts there. Neither owns any state of its
own, and nothing here writes to the cache — the driver loop is the only
writer (`market-data-design.md` §4).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...market_data.cache import PriceCache

logger = logging.getLogger("market_data.api")

router = APIRouter()

#: How often the stream *checks* the cache for advanced `updated_at`s. This
#: is deliberately not the emit cadence — see `price_event_stream`.
STREAM_CHECK_INTERVAL_SECONDS = 0.5
#: Comment heartbeat, so idle connections and intermediate proxies stay open.
HEARTBEAT_INTERVAL_SECONDS = 15.0


def _format_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def price_event_stream(
    cache: PriceCache,
    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
    *,
    check_interval_seconds: float = STREAM_CHECK_INTERVAL_SECONDS,
    heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    """
    The SSE body, as a plain async generator over a `PriceCache`.

    On connect it yields one event per tracked ticker carrying the current
    cache state, so a fresh client is fully populated without waiting for a
    tick — and, because `EventSource` reconnects on its own, every reconnect
    re-runs that snapshot and no client-side catch-up logic is needed
    (`PLAN.md` §6).

    Thereafter a ticker's event is emitted **only when its `updated_at` has
    advanced since the last event sent on this connection**. `last_sent_at`
    is per-connection state (a local, not a module global), which is what
    makes that true per client rather than per process.

    The distinction that matters: `check_interval_seconds` is the *check*
    cadence, not the *emit* cadence. Under Massive's 15-second poll,
    `updated_at` advances once every 15s, so ~29 of every 30 checks emit
    nothing. Without this gate the stream would push ~30 identical events
    per real tick, each with `prev_price == price`, which suppresses the
    frontend's flash animation and packs the sparklines with duplicates.

    Factored out of the route handler on purpose: as a module-level
    generator over a cache it can be driven with `__anext__()` in tests
    (`market-data-design.md` §15) without spinning up a real
    `StreamingResponse` or an HTTP client.
    """
    last_sent_at: dict[str, float] = {}

    # Initial full snapshot — every tracked ticker, regardless of updated_at.
    for entry in cache.snapshot():
        yield _format_event(entry.to_sse_event())
        last_sent_at[entry.ticker] = entry.updated_at

    last_heartbeat = time.monotonic()
    while True:
        if is_disconnected is not None and await is_disconnected():
            break

        for entry in cache.snapshot():
            sent_at = last_sent_at.get(entry.ticker)
            if sent_at is None or entry.updated_at > sent_at:
                yield _format_event(entry.to_sse_event())
                last_sent_at[entry.ticker] = entry.updated_at

        # Drop bookkeeping for tickers that left the tracked set, so a
        # ticker re-tracked later is treated as "never sent" and gets a
        # fresh snapshot event rather than being silently withheld because
        # its old `updated_at` was higher.
        for ticker in list(last_sent_at):
            if cache.get(ticker) is None:
                del last_sent_at[ticker]

        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_interval_seconds:
            yield ": ping\n\n"
            last_heartbeat = now

        await asyncio.sleep(check_interval_seconds)


@router.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    cache: PriceCache = request.app.state.price_cache

    return StreamingResponse(
        price_event_stream(cache, request.is_disconnected),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering if fronted by one
        },
    )


@router.get("/api/prices/{ticker}/history")
async def get_price_history(ticker: str, request: Request) -> dict:
    """
    Recent price points for a tracked ticker, oldest first, straight from
    the cache's in-memory ring buffer.

    This exists so the main detail chart has shape the moment a ticker is
    selected and after every page reload, instead of being empty until the
    next tick (`PLAN.md` §8). The buffer is in-memory only, so a fresh
    container legitimately returns `points: []` — that is a `200`, not an
    error, and the chart simply fills in from SSE.
    """
    cache: PriceCache = request.app.state.price_cache
    entry = cache.get(ticker.strip().upper())
    if entry is None:
        # Untracked tickers are pruned from the cache every driver cycle, so
        # a plain miss is the whole check. Note a ticker can 404 in a narrow
        # race right after leaving the watchlist with no position behind it —
        # correct per the tracked-set definition, not a bug: an untracked
        # ticker has no price history to backfill a chart with.
        raise HTTPException(status_code=404, detail=f"{ticker} is not tracked")

    return {
        "ticker": entry.ticker,
        "reference_price": entry.reference_price,
        "reference_kind": entry.reference_kind.value if entry.reference_kind else None,
        "points": [{"t": point.t, "price": point.price} for point in entry.history],
    }
