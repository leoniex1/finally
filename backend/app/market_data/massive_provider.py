"""`MassiveProvider` — a thin `MarketDataProvider` adapter over Massive's
REST snapshot endpoint (`MASSIVE_API.md` §5.1/§9, `MARKET_INTERFACE.md`
§4.2).
"""

from __future__ import annotations

import logging
import math
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
    price = _as_finite_positive_float(last_trade.get("p"))
    if not ticker or price is None:
        return None  # malformed row for this ticker; treat like "absent"

    reference_price = _as_finite_positive_float((row.get("prevDay") or {}).get("c"))
    return Quote(
        ticker=ticker,
        price=price,
        reference_price=reference_price,
        reference_kind=ReferenceKind.PREV_CLOSE if reference_price is not None else None,
    )


def _as_finite_positive_float(value: object) -> Optional[float]:
    """Coerce a wire value to a float, rejecting anything that would violate
    the `Quote` contract (`MARKET_INTERFACE.md` §2: price/reference_price
    must be real, finite, and positive when present). A malformed field
    (wrong type, non-numeric string, NaN/inf, zero, negative) is treated the
    same as an absent one rather than raising — `fetch()` must never fail an
    entire poll over one bad row (`MARKET_INTERFACE.md` §3), and a `0`/negative
    price must never reach the cache, since that would let a trade fill at a
    fabricated price (the "free shares" failure mode `PLAN.md` §8 forbids)."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed
