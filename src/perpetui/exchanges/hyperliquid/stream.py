from __future__ import annotations

import asyncio
import json
import logging
import time
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
from perpetui.exchanges.hyperliquid.parser import apply_market, parse_positions
from perpetui.exchanges.websocket import HistoryGap, decode_message

logger = logging.getLogger("perpetui")


class HyperliquidClientStreams:
    async def _heartbeat(self, websocket) -> None:
        while True:
            await websocket.send('{"method":"ping"}')
            await asyncio.sleep(self.heartbeat)

    async def _watch(
        self,
        subscriptions: list[dict[str, Any]],
        load: Callable[[], Awaitable[Any]],
        apply: Callable[[Any, dict[str, Any]], Any],
        seed: Any = None,
        idle_ok: bool = False,
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
                        for subscription in subscriptions:
                            await ws.send(
                                json.dumps({"method": "subscribe", "subscription": subscription})
                            )
                        state = await load() if retry or seed is None else seed
                        if retry:
                            yield "reconnecting", state
                        last_update = time.monotonic()
                        while True:
                            message = decode_message(
                                await asyncio.wait_for(ws.recv(), self.stale_after)
                            )
                            if message.get("channel") == "error":
                                raise ExchangeError("Hyperliquid WebSocket subscription rejected")
                            if not idle_ok and time.monotonic() - last_update > self.stale_after:
                                raise TimeoutError("Hyperliquid updates stopped")
                            try:
                                updated = apply(state, message)
                            except HistoryGap:
                                state = await load()
                                yield "live", state
                                last_update = time.monotonic()
                                continue
                            if updated is not None:
                                state = updated
                                last_update = time.monotonic()
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
                    "Hyperliquid WebSocket interrupted (%s); reconnecting", type(exc).__name__
                )
                retry = True
                yield "reconnecting", None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def stream_market(self, seed: Market) -> AsyncIterator[MarketEvent]:
        subscriptions = [
            {"type": "candle", "coin": seed.symbol, "interval": seed.interval},
            {"type": "activeAssetCtx", "coin": seed.symbol},
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
            raise ExchangeError("A Hyperliquid address is required for positions")

        def apply(previous, message):
            if message.get("channel") != "allDexsClearinghouseState":
                return None
            data = message["data"]
            if data["user"].lower() != self.account.address.lower():
                return None
            incoming = dict(data["clearinghouseStates"])
            merged = {}
            for dex, state in incoming.items():
                old = self._states.get(dex, {})
                merged[dex] = old if int(state.get("time", 0)) < int(old.get("time", 0)) else state
            result = parse_positions(merged, self._metadata)
            self._states = merged
            return result

        async with aclosing(
            self._watch(
                [{"type": "allDexsClearinghouseState", "user": self.account.address}],
                self.positions,
                apply,
                idle_ok=True,
            )
        ) as events:
            async for state, positions in events:
                yield PositionsEvent(state, positions)
