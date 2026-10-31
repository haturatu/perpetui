from __future__ import annotations

import asyncio
import gzip
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager, suppress
from decimal import Decimal
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from perpetui.errors import ExchangeError

logger = logging.getLogger("perpetui")


class HistoryGap(ValueError):
    """A stream skipped one or more candle intervals."""


def decode_message(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes) and raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    message = json.loads(raw, parse_float=Decimal)
    if not isinstance(message, dict):
        raise ValueError("Invalid WebSocket message")
    return message


STREAM_ERRORS = (
    WebSocketException,
    OSError,
    TimeoutError,
    ExchangeError,
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    EOFError,
)


@asynccontextmanager
async def websocket_connection(
    url: str, open_timeout: float, heartbeat: Callable[[ClientConnection], Awaitable[None]]
) -> AsyncIterator[ClientConnection]:
    """Always join the heartbeat before closing the transport."""
    async with connect(
        url,
        open_timeout=open_timeout,
        close_timeout=1,
        ping_interval=None,
        max_size=4 * 1024 * 1024,
    ) as websocket:
        task = asyncio.create_task(heartbeat(websocket))
        try:
            yield websocket
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError, WebSocketException, OSError):
                await task


async def retrying_stream(
    attempt: Callable[[bool], AsyncIterator[tuple[str, Any]]], *, exchange: str, retry_delay: float
) -> AsyncIterator[tuple[str, Any]]:
    """Share retry/cancellation policy, leaving subscriptions in each adapter."""
    retry, delay = False, retry_delay
    while True:
        yield ("reconnecting" if retry else "connecting"), None
        try:
            async with aclosing(attempt(retry)) as events:
                async for state, snapshot in events:
                    if state == "live":
                        delay = retry_delay
                    yield state, snapshot
        except STREAM_ERRORS as exc:
            logger.warning(
                "%s WebSocket interrupted (%s); reconnecting", exchange, type(exc).__name__
            )
        retry = True
        yield "reconnecting", None
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
