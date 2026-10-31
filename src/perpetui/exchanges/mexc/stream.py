from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from perpetui.domain.history import MAX_HISTORY
from perpetui.domain.models import (
    Candle,
    Market,
    MarketEvent,
    Positions,
    PositionsEvent,
    number,
)
from perpetui.errors import MEXCError
from perpetui.exchanges.mexc.account import Credentials
from perpetui.exchanges.mexc.intervals import MEXC_INTERVALS
from perpetui.exchanges.mexc.positions import PositionBook
from perpetui.exchanges.websocket import HistoryGap, decode_message

DEFAULT_WS_URL = "wss://contract.mexc.com/edge"
logger = logging.getLogger("perpetui")


def apply_message(market: Market, message: dict[str, Any]) -> Market:
    """Replace cumulative OHLCV for a bucket; never add its volume twice."""
    channel, data = message.get("channel"), message.get("data")
    if channel not in {"push.kline", "push.ticker"}:
        return market
    if not isinstance(data, dict):
        raise ValueError("Invalid WebSocket market data")
    if data.get("symbol", message.get("symbol")) != market.symbol:
        return market
    if channel == "push.ticker":
        fields = {
            "last": "lastPrice",
            "mark": "fairPrice",
            "high": "high24Price",
            "low": "lower24Price",
            "amount": "amount24",
        }
        updates = {field: number(data[key]) for field, key in fields.items() if key in data}
        if "riseFallRate" in data:
            updates["change"] = number(data["riseFallRate"]) * 100
        return replace(market, **updates)
    exchange_interval, seconds = MEXC_INTERVALS[market.interval]
    if data.get("interval") != exchange_interval:
        return market
    candle = Candle(
        int(data["t"]),
        *(number(data[key]) for key in ("o", "h", "l", "c")),
        number(data.get("v", data.get("q"))),
    )
    if (
        candle.low <= 0
        or candle.volume < 0
        or candle.high < max(candle.open, candle.close)
        or candle.low > min(candle.open, candle.close)
    ):
        raise ValueError("Invalid WebSocket candle")
    if market.candles and candle.time < market.candles[-1].time:
        return market
    if market.candles and candle.time > market.candles[-1].time + seconds:
        raise HistoryGap("Candle history needs repair")
    candles = list(market.candles)
    if candles and candles[-1].time == candle.time:
        candles[-1] = candle
    else:
        candles.append(candle)
    return replace(market, candles=candles[-MAX_HISTORY:], last=candle.close)


class MarketStream:
    def __init__(
        self,
        load_market: Callable[[str, str], Awaitable[Market]],
        *,
        url: str = DEFAULT_WS_URL,
        timeout: float = 10,
        heartbeat: float = 15,
        stale_after: float = 45,
        retry_delay: float = 1,
    ) -> None:
        self.load_market = load_market
        self.url = url
        self.timeout = timeout
        self.heartbeat = heartbeat
        self.stale_after = stale_after
        self.retry_delay = retry_delay

    async def _heartbeat(self, websocket: ClientConnection) -> None:
        while True:
            await websocket.send('{"method":"ping"}')
            await asyncio.sleep(self.heartbeat)

    async def watch(self, seed: Market) -> AsyncIterator[MarketEvent]:
        market = seed
        retry = False
        delay = self.retry_delay
        while True:
            yield MarketEvent("reconnecting" if retry else "connecting")
            try:
                # Every new connection after a loss gets authoritative history again.
                if retry:
                    market = await self.load_market(seed.symbol, seed.interval)
                    yield MarketEvent("reconnecting", market)
                async with connect(
                    self.url,
                    open_timeout=self.timeout,
                    close_timeout=1,
                    ping_interval=None,
                    max_size=1024 * 1024,
                ) as websocket:
                    for method, params in (
                        (
                            "sub.kline",
                            {"symbol": seed.symbol, "interval": MEXC_INTERVALS[seed.interval][0]},
                        ),
                        ("sub.ticker", {"symbol": seed.symbol}),
                    ):
                        await websocket.send(
                            json.dumps({"method": method, "param": params, "gzip": False})
                        )
                    heartbeat = asyncio.create_task(self._heartbeat(websocket))
                    last_push = time.monotonic()
                    last_timestamps: dict[str, int] = {}
                    last_price_timestamp = 0
                    try:
                        while True:
                            raw = await asyncio.wait_for(websocket.recv(), self.stale_after)
                            message = decode_message(raw)
                            channel = message.get("channel", "")
                            if (
                                channel == "rs.error"
                                or message.get("code") not in (None, 0, "0")
                                or (
                                    channel in {"rs.sub.kline", "rs.sub.ticker"}
                                    and message.get("data") != "success"
                                )
                            ):
                                raise MEXCError("WebSocket subscription rejected")
                            if time.monotonic() - last_push >= self.stale_after:
                                raise TimeoutError("WebSocket market updates stopped")
                            data = message.get("data")
                            stamp = int(
                                message.get("ts")
                                or (data.get("timestamp", 0) if isinstance(data, dict) else 0)
                            )
                            if stamp and stamp < last_timestamps.get(channel, 0):
                                continue
                            try:
                                updated = apply_message(market, message)
                            except HistoryGap:
                                market = await self.load_market(seed.symbol, seed.interval)
                                yield MarketEvent("live", market)
                                last_push = time.monotonic()
                                continue
                            if updated is not market:
                                price_event = channel == "push.kline" or (
                                    channel == "push.ticker" and "lastPrice" in data
                                )
                                if price_event and stamp:
                                    if stamp < last_price_timestamp:
                                        # Ticker snapshots can lag the kline feed by seconds.
                                        # Keep their mark / 24h data without rewinding price.
                                        updated = replace(updated, last=market.last)
                                    else:
                                        last_price_timestamp = stamp
                                market = updated
                                last_push = time.monotonic()
                                if stamp:
                                    last_timestamps[channel] = stamp
                                delay = self.retry_delay
                                yield MarketEvent("live", market)
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError, WebSocketException, OSError):
                            await heartbeat
            except (
                WebSocketException,
                OSError,
                TimeoutError,
                MEXCError,
                ValueError,
                KeyError,
                TypeError,
                EOFError,
            ) as exc:
                # Exception text may contain proxy credentials or response bodies.
                logger.warning(
                    "Market WebSocket interrupted (%s); reconnecting", type(exc).__name__
                )
                retry = True
                yield MarketEvent("reconnecting")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)


class PositionsStream:
    def __init__(
        self,
        credentials: Credentials,
        load_positions: Callable[[], Awaitable[Positions]],
        load_details: Callable[[], Awaitable[dict[str, dict[str, Any]]]],
        *,
        url: str = DEFAULT_WS_URL,
        timeout: float = 10,
        heartbeat: float = 15,
        stale_after: float = 45,
        retry_delay: float = 1,
    ) -> None:
        self.credentials = credentials
        self.load_positions = load_positions
        self.load_details = load_details
        self.url = url
        self.timeout = timeout
        self.heartbeat = heartbeat
        self.stale_after = stale_after
        self.retry_delay = retry_delay

    async def _heartbeat(self, websocket: ClientConnection) -> None:
        while True:
            await websocket.send('{"method":"ping"}')
            await asyncio.sleep(self.heartbeat)

    @staticmethod
    def _check_response(message: dict[str, Any]) -> None:
        channel = message.get("channel", "")
        if not isinstance(channel, str):
            raise ValueError("Invalid positions WebSocket channel")
        if (
            channel == "rs.error"
            or message.get("code") not in (None, 0, "0")
            or (channel.startswith("rs.") and message.get("data") != "success")
        ):
            raise MEXCError("Positions WebSocket request rejected")

    async def _expect(
        self, websocket: ClientConnection, channel: str, pending: deque[dict[str, Any]]
    ) -> None:
        async with asyncio.timeout(self.timeout):
            while True:
                message = decode_message(await websocket.recv())
                self._check_response(message)
                if message.get("channel") == channel:
                    return
                pending.append(message)

    async def _subscribe_prices(
        self,
        websocket: ClientConnection,
        symbols: set[str],
        previous: set[str],
        pending: deque[dict[str, Any]],
    ) -> None:
        for method, changed in (
            ("unsub.fair.price", previous - symbols),
            ("sub.fair.price", symbols - previous),
        ):
            for symbol in sorted(changed):
                await websocket.send(
                    json.dumps(
                        {
                            "method": method,
                            "param": {"symbol": symbol},
                            "gzip": False,
                        }
                    )
                )
                await self._expect(websocket, "rs." + method, pending)

    async def watch(self) -> AsyncIterator[PositionsEvent]:
        retry = False
        delay = self.retry_delay
        while True:
            yield PositionsEvent("reconnecting" if retry else "connecting")
            try:
                async with connect(
                    self.url,
                    open_timeout=self.timeout,
                    close_timeout=1,
                    ping_interval=None,
                    max_size=1024 * 1024,
                ) as websocket:
                    heartbeat = asyncio.create_task(self._heartbeat(websocket))
                    try:
                        pending: deque[dict[str, Any]] = deque()
                        stamp = str(int(time.time() * 1000))
                        signature = hmac.new(
                            self.credentials.api_secret.encode(),
                            (self.credentials.api_key + stamp).encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        await websocket.send(
                            json.dumps(
                                {
                                    "method": "login",
                                    "param": {
                                        "apiKey": self.credentials.api_key,
                                        "reqTime": stamp,
                                        "signature": signature,
                                        "subscribe": False,
                                    },
                                }
                            )
                        )
                        await self._expect(websocket, "rs.login", pending)
                        await websocket.send(
                            json.dumps(
                                {
                                    "method": "personal.filter",
                                    "param": {"filters": [{"filter": "position"}]},
                                }
                            )
                        )
                        await self._expect(websocket, "rs.personal.filter", pending)
                        # Subscribe before the snapshot; changes during REST wait in the socket.
                        details = await self.load_details()
                        book = PositionBook(await self.load_positions(), details)
                        await self._subscribe_prices(websocket, book.symbols, set(), pending)
                        yield PositionsEvent("live", book.snapshot())
                        delay = self.retry_delay
                        while True:
                            message = (
                                pending.popleft()
                                if pending
                                else decode_message(
                                    await asyncio.wait_for(websocket.recv(), self.stale_after)
                                )
                            )
                            self._check_response(message)
                            previous = book.symbols
                            if book.apply(message):
                                await self._subscribe_prices(
                                    websocket,
                                    book.symbols,
                                    previous,
                                    pending,
                                )
                                yield PositionsEvent("live", book.snapshot())
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError, WebSocketException, OSError):
                            await heartbeat
            except (
                WebSocketException,
                OSError,
                TimeoutError,
                MEXCError,
                ValueError,
                KeyError,
                TypeError,
                EOFError,
            ) as exc:
                logger.warning(
                    "Positions WebSocket interrupted (%s); reconnecting",
                    type(exc).__name__,
                )
                retry = True
                yield PositionsEvent("reconnecting")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
