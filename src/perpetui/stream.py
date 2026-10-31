"""Public Futures WebSocket candles and ticker, with heartbeat and history repair."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .errors import MEXCError
from .models import INTERVALS, Candle, Market, MarketEvent, number

DEFAULT_WS_URL = "wss://contract.mexc.com/edge"
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
    exchange_interval, seconds = INTERVALS[market.interval]
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
    return replace(market, candles=candles[-160:], last=candle.close)


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
                            {"symbol": seed.symbol, "interval": INTERVALS[seed.interval][0]},
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
