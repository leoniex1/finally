"""The unified market data interface (`MARKET_INTERFACE.md` §2–§3).

This is the Python contract that lets the rest of the backend (SSE stream,
trade executor, portfolio valuation) ask for prices without knowing or
caring whether Massive or the simulator is answering. `fetch()` is a single
pull request/response round — it does not loop, sleep, or own a schedule;
scheduling belongs to the shared driver (`driver.py`), which is what makes
both implementations trivially unit-testable in isolation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AbstractSet, Mapping, Optional

from .models import ReferenceKind

__all__ = ["Quote", "MarketDataProvider", "ReferenceKind"]


@dataclass(frozen=True, slots=True)
class Quote:
    """One source's answer for one ticker, for one `fetch()` call."""

    ticker: str
    price: float
    reference_price: Optional[float] = None
    reference_kind: Optional[ReferenceKind] = None


class MarketDataProvider(ABC):
    """
    The one interface every price source implements. `fetch()` is a single
    request/response round — it does not loop, sleep, or own a schedule.
    Scheduling is the driver's job (`driver.py`), not the provider's, which
    is what makes both implementations trivially unit-testable: call
    `fetch()` once with a known ticker set and assert on the dict you get
    back.
    """

    #: How often the driver should call `fetch()` for this provider, in
    #: seconds. A class attribute (constant) for the simulator; an instance
    #: attribute for Massive, since it's derived from
    #: MASSIVE_POLL_INTERVAL_SECONDS at construction time.
    poll_interval_seconds: float

    @abstractmethod
    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        """
        Fetch current data for exactly the given tickers, once.

        Returns a dict keyed by ticker. A ticker in `tickers` that is absent
        from the returned dict means the source has no data for it *right
        now*. Never raises for an individual bad/unknown ticker; only
        raises for a whole-request failure (network error, auth failure,
        malformed response) that the caller should treat as "this cycle
        produced nothing usable, try again next cycle".
        """
        raise NotImplementedError
