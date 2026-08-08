from __future__ import annotations

import asyncio

import pytest

from app.market_data.supervisor import run_supervised


async def test_restarts_after_exception() -> None:
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError()  # deterministic way to end the loop

    with pytest.raises(asyncio.CancelledError):
        await run_supervised(
            "test",
            flaky,
            initial_backoff_seconds=0.001,
            max_backoff_seconds=0.001,
        )

    assert calls == 2


async def test_restarts_after_unexpected_clean_return() -> None:
    calls = 0

    async def returns_then_cancels() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return  # a clean return is treated as a bug too
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_supervised(
            "test",
            returns_then_cancels,
            initial_backoff_seconds=0.001,
            max_backoff_seconds=0.001,
        )

    assert calls == 2


async def test_cancellation_is_not_swallowed() -> None:
    async def cancels_immediately() -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_supervised(
            "test",
            cancels_immediately,
            initial_backoff_seconds=0.001,
            max_backoff_seconds=0.001,
        )


async def test_backoff_grows_and_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    async def always_raises() -> None:
        nonlocal calls
        calls += 1
        if calls >= 4:
            raise asyncio.CancelledError()
        raise RuntimeError("boom")

    async def recording_sleep(delay: float) -> None:
        sleeps.append(delay)  # don't actually wait in the test

    monkeypatch.setattr("app.market_data.supervisor.asyncio.sleep", recording_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_supervised(
            "test",
            always_raises,
            initial_backoff_seconds=1.0,
            max_backoff_seconds=3.0,
        )

    assert sleeps == [1.0, 2.0, 3.0]  # doubles each time, capped at max
