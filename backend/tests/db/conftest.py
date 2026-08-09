from __future__ import annotations

from typing import AsyncIterator

import aiosqlite
import pytest

from app.db.connection import connect
from app.db.init import init_db


@pytest.fixture
async def db_path(tmp_path) -> str:
    """A freshly initialized, seeded database file."""
    path = str(tmp_path / "finally.db")
    await init_db(path)
    return path


@pytest.fixture
async def db(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """An open connection carrying the app's pragmas."""
    async with connect(db_path) as connection:
        yield connection
