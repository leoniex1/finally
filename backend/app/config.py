"""Environment-driven configuration for the whole backend (`PLAN.md` §5,
`API_CONTRACT.md` §5.2).

Configuration is always read from the process environment. Under Docker the
values arrive via `--env-file`, so there is no `.env` inside the container;
for host development `from_env()` additionally loads a project-root `.env`
when one happens to be there. A missing `.env` is never an error, and a
value already present in the environment always wins over the file — that
ordering is what keeps `docker run -e ...` authoritative.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("app.config")

DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS = 15.0
DEFAULT_DB_PATH = "db/finally.db"
DEFAULT_LLM_MODEL = "openrouter/openai/gpt-oss-120b"
DEFAULT_STATIC_DIR = "static"

#: Values that make a boolean env var true. Anything else — including the
#: empty string left by an unfilled `.env` template — is false, so a stray
#: `LLM_MOCK=` never silently disables the real LLM.
_TRUTHY = frozenset({"true", "1"})


@dataclass(frozen=True)
class Settings:
    massive_api_key: str = ""
    massive_poll_interval_seconds: float = DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS
    #: Where the SQLite file lives. `TrackedSetProvider` reads the watchlist
    #: and positions tables from it every driver cycle (`PLAN.md` §6, §11).
    db_path: str = DEFAULT_DB_PATH
    #: Absent key disables chat only (`/api/chat` → 503). It must never
    #: prevent startup — `PLAN.md` §5.
    openrouter_api_key: str = ""
    llm_mock: bool = False
    llm_model: str = DEFAULT_LLM_MODEL
    #: Directory holding the Next.js static export. Absent in backend-only
    #: development, which is a warning rather than an error (§5.3).
    static_dir: str = DEFAULT_STATIC_DIR

    @classmethod
    def from_env(cls) -> "Settings":
        load_project_dotenv()
        return cls(
            massive_api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
            massive_poll_interval_seconds=float(
                os.environ.get(
                    "MASSIVE_POLL_INTERVAL_SECONDS",
                    str(DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS),
                )
            ),
            db_path=os.environ.get("DB_PATH", DEFAULT_DB_PATH),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
            llm_mock=os.environ.get("LLM_MOCK", "").strip().lower() in _TRUTHY,
            llm_model=os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL),
            static_dir=os.environ.get("STATIC_DIR", DEFAULT_STATIC_DIR),
        )

    @property
    def use_massive(self) -> bool:
        return bool(self.massive_api_key)

    @property
    def chat_enabled(self) -> bool:
        """Mock mode is a complete substitute for the key: the E2E suite and
        offline development run with no OpenRouter account at all."""
        return bool(self.openrouter_api_key) or self.llm_mock


def dotenv_candidates() -> tuple[Path, ...]:
    """Where a host-development `.env` might live, in precedence order: the
    process working directory (where a developer runs `uv run uvicorn`), the
    repository root, then `backend/`."""
    module_dir = Path(__file__).resolve().parent  # backend/app
    return (
        Path.cwd() / ".env",
        module_dir.parent.parent / ".env",  # repo root
        module_dir.parent / ".env",  # backend/
    )


def find_project_dotenv() -> Optional[Path]:
    """The first existing candidate, or `None` when there is none — the
    Docker case, and not an error."""
    for candidate in dotenv_candidates():
        if candidate.is_file():
            return candidate
    return None


def load_project_dotenv() -> Optional[Path]:
    """Load a project-root `.env` into `os.environ` if one exists.

    `override=False`: variables already in the environment win. Docker passes
    everything through the environment, so a leftover `.env` baked into an
    image (or a developer's stale file) can never override an explicit
    `-e`/`--env-file` value.
    """
    dotenv_path = find_project_dotenv()
    if dotenv_path is None:
        return None

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        logger.warning("python-dotenv is not installed; ignoring %s", dotenv_path)
        return None

    load_dotenv(dotenv_path, override=False)
    logger.info("loaded environment defaults from %s", dotenv_path)
    return dotenv_path
