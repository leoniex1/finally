"""Service-layer rejections carrying their own HTTP status
(`API_CONTRACT.md` §5).

These exist because the trade and watchlist rules have **two** entry points:
the REST routes and the LLM action executor, which calls the service
functions directly rather than over HTTP (`PLAN.md` §9). An `HTTPException`
raised inside the service would force llm-engineer to import FastAPI to read
a rejection reason; a bare `ValueError` would force every caller to re-derive
the status code. So the service raises these, the route maps them to
`HTTPException`, and the executor maps the same objects onto its `actions`
array — with identical `detail` text on both paths, which is the whole point.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base for a rejection that already knows its HTTP status.

    `detail` is user-facing: the frontend renders it verbatim in an inline
    error slot, and the chat panel shows it as the `error` of a failed
    action (`API_CONTRACT.md` §2, §3.8).
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TradeError(ServiceError):
    """A trade rejected by the §3.3 ladder. `status_code` is 400, 409 or 422."""


class WatchlistError(ServiceError):
    """A watchlist change rejected by the §3.6/§3.7 rules. `status_code` is 400."""


class HistoryError(ServiceError):
    """Bad `since`/`limit` on `GET /api/portfolio/history` (§3.4). Always 400."""
