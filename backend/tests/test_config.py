"""Settings resolution (`API_CONTRACT.md` §5.2, `PLAN.md` §5).

The load-bearing behaviours here are that the process environment always
wins over a `.env` file (Docker passes everything through the environment),
and that a missing `.env` is never an error.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from app.config import (
    DEFAULT_DB_PATH,
    DEFAULT_LLM_MODEL,
    DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS,
    DEFAULT_STATIC_DIR,
    Settings,
    find_project_dotenv,
    load_project_dotenv,
)

ENV_VARS = (
    "MASSIVE_API_KEY",
    "MASSIVE_POLL_INTERVAL_SECONDS",
    "DB_PATH",
    "OPENROUTER_API_KEY",
    "LLM_MOCK",
    "LLM_MODEL",
    "STATIC_DIR",
)


@pytest.fixture
def isolated_env() -> Iterator[None]:
    """A pristine environment, restored wholesale afterwards.

    `load_dotenv` writes straight into `os.environ`, which `monkeypatch`
    cannot undo for keys it never saw, so these tests snapshot and restore
    the whole mapping instead.
    """
    saved = dict(os.environ)
    for name in ENV_VARS:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend there is no `.env` anywhere — the Docker case. Without this
    the developer's own repo-root `.env` would leak into the assertions."""
    monkeypatch.setattr("app.config.find_project_dotenv", lambda: None)


# ── Defaults ─────────────────────────────────────────────────────────────


def test_defaults(isolated_env: None, no_dotenv: None) -> None:
    settings = Settings.from_env()

    assert settings.massive_api_key == ""
    assert settings.massive_poll_interval_seconds == DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS
    assert settings.db_path == DEFAULT_DB_PATH
    assert settings.openrouter_api_key == ""
    assert settings.llm_mock is False
    assert settings.llm_model == DEFAULT_LLM_MODEL
    assert settings.static_dir == DEFAULT_STATIC_DIR


def test_reads_every_variable(isolated_env: None, no_dotenv: None) -> None:
    os.environ.update(
        {
            "MASSIVE_API_KEY": "  mk  ",
            "MASSIVE_POLL_INTERVAL_SECONDS": "2.5",
            "DB_PATH": "/data/finally.db",
            "OPENROUTER_API_KEY": "  or-key  ",
            "LLM_MOCK": "true",
            "LLM_MODEL": "openrouter/some/other-model",
            "STATIC_DIR": "/app/static",
        }
    )
    settings = Settings.from_env()

    assert settings.massive_api_key == "mk"  # trimmed
    assert settings.massive_poll_interval_seconds == 2.5
    assert settings.db_path == "/data/finally.db"
    assert settings.openrouter_api_key == "or-key"  # trimmed
    assert settings.llm_mock is True
    assert settings.llm_model == "openrouter/some/other-model"
    assert settings.static_dir == "/app/static"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("1", True),
        (" true ", True),
        ("false", False),
        ("0", False),
        ("", False),  # an unfilled `.env` template line
        ("yes", False),
        ("no", False),
    ],
)
def test_llm_mock_parsing(
    isolated_env: None, no_dotenv: None, raw: str, expected: bool
) -> None:
    os.environ["LLM_MOCK"] = raw
    assert Settings.from_env().llm_mock is expected


# ── Derived flags ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "expected"),
    [("", False), ("some-key", True)],
)
def test_use_massive_follows_the_key(key: str, expected: bool) -> None:
    assert Settings(massive_api_key=key).use_massive is expected


@pytest.mark.parametrize(
    ("key", "mock", "expected"),
    [
        ("", False, False),
        ("or-key", False, True),
        ("", True, True),  # mock mode is a complete substitute for the key
        ("or-key", True, True),
    ],
)
def test_chat_enabled(key: str, mock: bool, expected: bool) -> None:
    assert Settings(openrouter_api_key=key, llm_mock=mock).chat_enabled is expected


# ── .env loading ─────────────────────────────────────────────────────────


@pytest.fixture
def only_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the `.env` search at one file under `tmp_path`, so the
    developer's own repo-root `.env` cannot leak into these assertions."""
    candidate = tmp_path / ".env"
    monkeypatch.setattr("app.config.dotenv_candidates", lambda: (candidate,))
    return candidate


def test_missing_dotenv_is_not_an_error(
    isolated_env: None, only_candidate: Path
) -> None:
    """The Docker path: no `.env` exists inside the container."""
    assert find_project_dotenv() is None
    assert load_project_dotenv() is None
    assert Settings.from_env().db_path == DEFAULT_DB_PATH


def test_dotenv_fills_in_absent_variables(
    isolated_env: None, only_candidate: Path
) -> None:
    only_candidate.write_text("DB_PATH=/from/dotenv.db\nLLM_MOCK=true\n", encoding="utf-8")

    settings = Settings.from_env()

    assert settings.db_path == "/from/dotenv.db"
    assert settings.llm_mock is True


def test_process_environment_beats_the_dotenv_file(
    isolated_env: None, only_candidate: Path
) -> None:
    """`docker run --env-file` / `-e` must stay authoritative even if a
    `.env` was baked into the image or left behind by a host run."""
    only_candidate.write_text("DB_PATH=/from/dotenv.db\n", encoding="utf-8")
    os.environ["DB_PATH"] = "/from/environment.db"

    assert Settings.from_env().db_path == "/from/environment.db"


def test_find_project_dotenv_prefers_the_working_directory(
    isolated_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first existing candidate wins, and `Path.cwd()` is first."""
    cwd_dotenv = tmp_path / "cwd" / ".env"
    root_dotenv = tmp_path / "root" / ".env"
    for dotenv in (cwd_dotenv, root_dotenv):
        dotenv.parent.mkdir()
        dotenv.write_text("DB_PATH=/x.db\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.config.dotenv_candidates", lambda: (cwd_dotenv, root_dotenv)
    )
    assert find_project_dotenv() == cwd_dotenv

    cwd_dotenv.unlink()
    assert find_project_dotenv() == root_dotenv
