"""Watchlist HTTP surface (`API_CONTRACT.md` §3.5, §3.6, §3.7).

- `GET    /api/watchlist`           — watched tickers with live prices
- `POST   /api/watchlist`           — add a ticker (idempotent)
- `DELETE /api/watchlist/{ticker}`  — remove a ticker (idempotent)

As in `portfolio.py`, these handlers only translate: cache off `app.state`,
call the service, map `ServiceError` to `HTTPException`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ...market_data.cache import PriceCache
from ...portfolio.errors import ServiceError
from ...portfolio.watchlist_service import add_ticker, get_watchlist, remove_ticker

router = APIRouter()


class WatchlistRequest(BaseModel):
    """The `POST /api/watchlist` body.

    `ticker` is `Any` for the same reason as `TradeRequest`'s fields: §3.6
    specifies `400` with the `detail` string `normalize_ticker` produces, and
    a Pydantic type error would surface as a `422` in FastAPI's own envelope
    instead — a different status and a different body for the same mistake,
    depending only on whether the client sent a number or a bad string.
    """

    ticker: Any = None


@router.get("/api/watchlist")
async def list_tickers(request: Request) -> dict[str, Any]:
    cache: PriceCache = request.app.state.price_cache
    return await get_watchlist(request.app.state.settings.db_path, cache)


@router.post("/api/watchlist")
async def add(payload: WatchlistRequest, request: Request) -> dict[str, Any]:
    cache: PriceCache = request.app.state.price_cache
    try:
        return await add_ticker(
            request.app.state.settings.db_path, cache, payload.ticker
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/api/watchlist/{ticker}", status_code=204)
async def remove(ticker: str, request: Request) -> Response:
    """`204` whether or not a row went — removing a ticker that is not on the
    list is not an error (§3.7). An explicit empty `Response` rather than
    `None`, so FastAPI does not try to serialize a body into a 204."""
    try:
        await remove_ticker(request.app.state.settings.db_path, ticker)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return Response(status_code=204)
