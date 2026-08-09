"""Portfolio HTTP surface (`API_CONTRACT.md` §3.2, §3.3, §3.4).

- `GET  /api/portfolio`          — positions, cash, total value, P&L
- `POST /api/portfolio/trade`    — execute a market order
- `GET  /api/portfolio/history`  — snapshots for the P&L chart

These handlers hold no logic of their own. They pull the shared price cache
off `app.state`, call the service, and translate `ServiceError` into
`HTTPException` — that is the whole job. The rules live in
`app/portfolio/service.py` because the LLM executor calls the same functions
directly, and a rule implemented in a route handler is a rule the chat path
does not get (`PLAN.md` §9).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ...market_data.cache import PriceCache
from ...portfolio.errors import ServiceError
from ...portfolio.service import build_portfolio, execute_trade, get_portfolio_history

router = APIRouter()


class TradeRequest(BaseModel):
    """The `POST /api/portfolio/trade` body.

    Every field is deliberately untyped (`Any`, defaulting to `None`) rather
    than `str`/`float`/`Literal`. This looks wrong and is load-bearing.

    §3.3 specifies a *normative order* for the validation ladder — quantity,
    then side, then ticker, then price, then funds — and specifies `400` for
    the first three. Pydantic would type-check all three fields at once,
    before the handler runs, and FastAPI would report the result as `422`
    with its own error envelope. That is the wrong status, the wrong body,
    and the wrong precedence: a request with both a bad side and a bad
    quantity would report whichever Pydantic listed first.

    Accepting the raw values and handing them to the ladder is what makes the
    documented order and the documented `detail` strings actually true, and
    keeps this path byte-identical to the LLM executor's, which never goes
    near Pydantic at all.
    """

    ticker: Any = None
    quantity: Any = None
    side: Any = None


@router.get("/api/portfolio")
async def get_portfolio(request: Request) -> dict[str, Any]:
    cache: PriceCache = request.app.state.price_cache
    return await build_portfolio(request.app.state.settings.db_path, cache)


@router.post("/api/portfolio/trade")
async def post_trade(payload: TradeRequest, request: Request) -> dict[str, Any]:
    cache: PriceCache = request.app.state.price_cache
    try:
        return await execute_trade(
            request.app.state.settings.db_path,
            cache,
            payload.ticker,
            payload.quantity,
            payload.side,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/api/portfolio/history")
async def get_history(
    request: Request,
    since: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None),
) -> dict[str, Any]:
    try:
        return await get_portfolio_history(
            request.app.state.settings.db_path, since=since, limit=limit
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
