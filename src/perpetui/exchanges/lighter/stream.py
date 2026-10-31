from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, suppress
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from perpetui.domain.models import (
    Market,
    MarketEvent,
    PositionsEvent,
)
from perpetui.errors import ExchangeError
from perpetui.exchanges.lighter.parser import apply_market, parse_positions
from perpetui.exchanges.websocket import HistoryGap, decode_message

logger = logging.getLogger("perpetui")


class LighterClientStreams:
    async def _heartbeat(self, websocket) -> None:
        while True:
            await websocket.send('{"type":"ping"}')
            await asyncio.sleep(self.heartbeat)

    async def _watch(
        self,
        subscriptions: list[str],
        load: Callable[[], Awaitable[Any]],
        apply: Callable[[Any, dict[str, Any]], Any],
        seed: Any = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        retry, delay = False, self.retry_delay
        while True:
            yield ("reconnecting" if retry else "connecting"), None
            try:
                async with connect(
                    self.websocket_url,
                    open_timeout=self.timeout,
                    close_timeout=1,
                    ping_interval=None,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    try:
                        while True:
                            message = decode_message(
                                await asyncio.wait_for(ws.recv(), self.stale_after)
                            )
                            kind = message.get("type")
                            if kind == "connected":
                                break
                            if kind == "ping":
                                await ws.send('{"type":"pong"}')
                        for channel in subscriptions:
                            await ws.send(json.dumps({"type": "subscribe", "channel": channel}))
                        state = await load() if retry or seed is None else seed
                        if retry:
                            yield "reconnecting", state
                        while True:
                            message = decode_message(
                                await asyncio.wait_for(ws.recv(), self.stale_after)
                            )
                            kind = message.get("type")
                            if kind == "ping":
                                await ws.send('{"type":"pong"}')
                                continue
                            if kind == "pong":
                                continue
                            if "error" in message:
                                raise ExchangeError("Lighter WebSocket request rejected")
                            try:
                                updated = apply(state, message)
                            except HistoryGap:
                                state = await load()
                                yield "live", state
                                continue
                            if updated is not None:
                                state = updated
                                delay = self.retry_delay
                                yield "live", state
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError, WebSocketException, OSError):
                            await heartbeat
            except (
                WebSocketException,
                OSError,
                TimeoutError,
                ExchangeError,
                ValueError,
                KeyError,
                TypeError,
                IndexError,
                EOFError,
            ) as exc:
                logger.warning(
                    "Lighter WebSocket interrupted (%s); reconnecting", type(exc).__name__
                )
                retry = True
                yield "reconnecting", None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def stream_market(self, seed: Market) -> AsyncIterator[MarketEvent]:
        metadata = await self._load_metadata()
        market_id = int(metadata[seed.symbol]["market_id"])
        subscriptions = [
            f"candle/{market_id}/{seed.interval}",
            f"market_stats/{market_id}",
        ]

        def apply(market, message):
            result = apply_market(market, message)
            return result if result is not market else None

        async with aclosing(
            self._watch(subscriptions, lambda: self.market(seed.symbol, seed.interval), apply, seed)
        ) as events:
            async for state, market in events:
                yield MarketEvent(state, market)

    async def stream_positions(self) -> AsyncIterator[PositionsEvent]:
        if not self.account:
            raise ExchangeError("A Lighter account index is required for positions")
        metadata = await self._load_metadata()

        def apply(previous, message):
            if not message.get("type", "").endswith("account_all_positions"):
                return None
            rows = message.get("positions")
            if not isinstance(rows, dict):
                raise ValueError("Invalid Lighter position update")
            return parse_positions(list(rows.values()), metadata)

        async with aclosing(
            self._watch(
                [f"account_all_positions/{self.account.account_index}"],
                self.positions,
                apply,
            )
        ) as events:
            async for state, positions in events:
                yield PositionsEvent(state, positions)
