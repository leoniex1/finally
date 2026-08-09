"""LLM integration (`PLAN.md` §9, `API_CONTRACT.md` §6).

Exactly one LLM call per user message, structured output, and a deterministic
mock mode that replaces *only* the call — execution and result reporting run
for real in both modes (`API_CONTRACT.md` §6.1).
"""

from __future__ import annotations

from .client import (
    LLMError,
    LLMUnavailableError,
    LLMUpstreamError,
    generate_response,
)
from .schemas import LLMResponse, TradeIntent, WatchlistChange

__all__ = [
    "LLMError",
    "LLMResponse",
    "LLMUnavailableError",
    "LLMUpstreamError",
    "TradeIntent",
    "WatchlistChange",
    "generate_response",
]
