import asyncio
from contextlib import aclosing

import pytest

from perpetui.exchanges.websocket import retrying_stream


async def test_retry_backoff_resets_on_live_and_does_not_log_payloads(monkeypatch, caplog):
    delays, attempts = [], []

    async def sleep(delay):
        delays.append(delay)

    async def attempt(retry):
        attempts.append(retry)
        if len(attempts) == 3:
            yield "live", "snapshot"
        raise ValueError("private payload must never be logged")

    monkeypatch.setattr("perpetui.exchanges.websocket.asyncio.sleep", sleep)
    seen = []
    async with aclosing(retrying_stream(attempt, exchange="test", retry_delay=1)) as stream:
        async for state, value in stream:
            seen.append((state, value))
            if len(delays) == 3:
                break
    assert attempts == [False, True, True]
    assert delays == [1, 2, 1]
    assert ("live", "snapshot") in seen
    assert "ValueError" in caplog.text and "private payload" not in caplog.text


async def test_cancelling_subscription_does_not_retry_and_joins_attempt_cleanup():
    started, stopped = asyncio.Event(), asyncio.Event()
    attempts = []

    async def attempt(retry):
        attempts.append(retry)
        started.set()
        try:
            await asyncio.Event().wait()
            yield "live", None
        finally:
            stopped.set()

    async with aclosing(retrying_stream(attempt, exchange="test", retry_delay=1)) as stream:
        assert await anext(stream) == ("connecting", None)
        pending = asyncio.create_task(anext(stream))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert stopped.is_set() and attempts == [False]
