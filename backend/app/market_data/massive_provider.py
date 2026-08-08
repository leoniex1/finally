"""`MassiveProvider` — a thin `MarketDataProvider` adapter over Massive's
REST snapshot endpoint (`MASSIVE_API.md` §5.1/§9, `MARKET_INTERFACE.md`
§4.2).
"""

from __future__ import annotations

import logging
from typing import AbstractSet, Mapping, Optional

import httpx

from .models import ReferenceKind
from .provider import MarketDataProvider, Quote

logger = logging.getLogger("market_data.massive")

MASSIVE_BASE_URL = "https://api.massive.com"
SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers"
REQUEST_TIMEOUT_SECONDS = 8.0


class MassiveProvider(MarketDataProvider):
    def __init__(self, api_key: str, poll_interval_seconds: float = 15.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._client = httpx.AsyncClient(
            base_url=MASSIVE_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        if not tickers:
            return {}

        response = await self._client.get(
            SNAPSHOT_PATH, params={"tickers": ",".join(sorted(tickers))}
        )
        response.raise_for_status()  # network/auth/rate-limit failures propagate to the driver
        payload = response.json()

        quotes: dict[str, Quote] = {}
        for row in payload.get("tickers", []):
            quote = _quote_from_row(row)
            if quote is not None:
                quotes[quote.ticker] = quote
        return quotes
        # Tickers requested but absent from payload["tickers"] are simply
        # not in the returned dict — that IS the "unavailable" signal. This
        # method does not need to know the full `tickers` set to produce a
        # correct result; the driver computes the difference.


def _quote_from_row(row: dict) -> Optional[Quote]:
    ticker = row.get("ticker")
    last_trade = row.get("lastTrade") or {}
    price = last_trade.get("p")
    if not ticker or price is None:
        return None  # malformed row for this ticker; treat like "absent"

    prev_close = (row.get("prevDay") or {}).get("c")
    return Quote(
        ticker=ticker,
        price=float(price),
        reference_price=float(prev_close) if prev_close is not None else None,
        reference_kind=ReferenceKind.PREV_CLOSE if prev_close is not None else None,
    )
