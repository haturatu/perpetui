"""Read-only Lighter perpetuals over the public REST and WebSocket APIs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .errors import ExchangeError
from .history import MAX_HISTORY
from .i18n import _
from .lighter_account import LighterAccount
from .models import (
    INTERVALS,
    Candle,
    Market,
    MarketEvent,
    Position,
    Positions,
    PositionsEvent,
    number,
)
from .stream import HistoryGap, decode_message

MAINNET_URL = "https://mainnet.zklighter.elliot.ai"
TESTNET_URL = "https://testnet.zklighter.elliot.ai"
LIGHTER_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
logger = logging.getLogger("perpetui")


def normalize_lighter_symbol(value: str) -> str:
    symbol = value.strip()
    symbol = re.sub(r"[/_](USDC|USD|USDT)$", "", symbol, flags=re.IGNORECASE)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", symbol):
        raise ValueError("Use a Lighter symbol such as BTC, HYPE or 1000PEPE")
    return symbol.upper()


def parse_candle(row: dict[str, Any], interval: str) -> Candle:
    bar = Candle(
        int(row["t"]) // 1000,
        *(number(row[key]) for key in ("o", "h", "l", "c")),
        number(row.get("v", 0)),
    )
    if (
        bar.low <= 0
        or bar.volume < 0
        or bar.high < max(bar.open, bar.close)
        or bar.low > min(bar.open, bar.close)
    ):
        raise ValueError("Invalid Lighter candle")
    return bar


def parse_positions(rows: list[dict[str, Any]], metadata: dict[str, dict[str, Any]]) -> Positions:
    positions = []
    for row in rows:
        size = number(row["position"])
        if size == 0:
            continue
        symbol = str(row["symbol"])
        detail = metadata.get(symbol, {})
        value = abs(number(row.get("position_value") or 0))
        # Account positions report a percentage (2.00 means 2% initial margin).
        fraction = number(row.get("initial_margin_fraction") or 0) / 100
        if fraction > 0:
            leverage = max(1, round(1 / fraction))
            margin = value * fraction
        else:
            minimum = number(detail.get("min_initial_margin_fraction") or 0)
            leverage = max(1, 10_000 // int(minimum)) if minimum > 0 else 1
            margin = value * minimum / 10_000 if minimum > 0 else Decimal(0)
        pnl = number(row.get("unrealized_pnl") or 0)
        positions.append(
            Position(
                id=symbol,
                symbol=symbol,
                side="LONG" if int(row["sign"]) > 0 else "SHORT",
                contracts=abs(size),
                entry=number(row["avg_entry_price"]),
                liquidation=number(row.get("liquidation_price") or 0),
                leverage=leverage,
                margin=margin,
                margin_mode="Isolated" if int(row.get("margin_mode", 0)) == 1 else "Cross",
                mark=value / abs(size),
                contract_size=Decimal(1),
                settlement="USDC",
                reported_pnl=pnl,
                reported_roe=pnl / margin * 100 if margin > 0 else None,
            )
        )
    return Positions(positions)


def _merge_candles(
    candles: list[Candle], rows: list[dict[str, Any]], interval: str
) -> list[Candle]:
    bars = list(candles)
    seconds = INTERVALS[interval][1]
    for row in sorted(rows, key=lambda row: int(row["t"])):
        candle = parse_candle(row, interval)
        if bars and candle.time < bars[-1].time:
            continue
        if bars and candle.time > bars[-1].time + seconds:
            raise HistoryGap("Lighter candle history needs repair")
        if bars and candle.time == bars[-1].time:
            if candle.volume < bars[-1].volume:
                continue
            bars[-1] = candle
        else:
            bars.append(candle)
    return bars[-MAX_HISTORY:]


def apply_market(market: Market, message: dict[str, Any]) -> Market:
    kind = message.get("type", "")
    if kind.endswith("market_stats"):
        stats = message.get("market_stats")
        if not isinstance(stats, dict) or stats.get("symbol") != market.symbol:
            return market
        mark = number(stats["mark_price"])
        if mark <= 0:
            raise ValueError("Invalid Lighter mark price")
        return replace(
            market,
            last=number(stats.get("last_trade_price") or market.last),
            mark=mark,
            change=number(stats.get("daily_price_change") or 0),
            high=number(stats["daily_price_high"]) if "daily_price_high" in stats else market.high,
            low=number(stats["daily_price_low"]) if "daily_price_low" in stats else market.low,
            amount=number(stats.get("daily_quote_token_volume") or 0),
        )
    if not kind.endswith("candle"):
        return market
    rows = message.get("candles")
    if not isinstance(rows, list):
        raise ValueError("Invalid Lighter candle update")
    bars = _merge_candles(market.candles, rows, market.interval)
    return replace(market, candles=bars, last=bars[-1].close) if bars != market.candles else market


class LighterClient:
    exchange = "Lighter"
    default_symbol = "BTC"
    quantity_label = "Size"
    public_hint = "Public market mode · configure lighter or LIGHTER_ACCOUNT_INDEX"
    intervals = LIGHTER_INTERVALS

    def __init__(
        self,
        account: LighterAccount | None = None,
        *,
        testnet: bool = False,
        base_url: str | None = None,
        websocket_url: str | None = None,
        timeout: float = 10,
        streaming: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        heartbeat: float = 15,
        stale_after: float = 45,
        retry_delay: float = 1,
    ) -> None:
        self.account = account
        self.authenticated = account is not None
        self.label = (account.label if account else _("public")) + (" · TESTNET" if testnet else "")
        self.streaming = streaming
        self.position_streaming = streaming and self.authenticated
        base_url = (base_url or (TESTNET_URL if testnet else MAINNET_URL)).rstrip("/")
        self.websocket_url = (
            websocket_url
            or base_url.replace("https://", "wss://").replace("http://", "ws://") + "/stream"
        )
        self.timeout = timeout
        self.heartbeat, self.stale_after, self.retry_delay = heartbeat, stale_after, retry_delay
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "perpetui/0.1.0"},
        )
        self._metadata: dict[str, dict[str, Any]] = {}
        self._by_id: dict[int, dict[str, Any]] = {}
        self._metadata_lock = asyncio.Lock()
        self._request_limit = asyncio.Semaphore(4)

    def normalize_symbol(self, value: str) -> str:
        symbol = normalize_lighter_symbol(value)
        return next((coin for coin in self._metadata if coin.upper() == symbol), symbol)

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        try:
            async with self._request_limit:
                response = await self._http.get(path, params=params)
        except httpx.RequestError as exc:
            raise ExchangeError(f"Lighter network error ({type(exc).__name__})") from exc
        if response.is_error:
            raise ExchangeError(f"Lighter HTTP {response.status_code} at {path}")
        try:
            payload = json.loads(response.text, parse_float=Decimal)
        except (ValueError, TypeError) as exc:
            raise ExchangeError("Invalid Lighter JSON response") from exc
        if not isinstance(payload, dict):
            raise ExchangeError("Lighter returned an unexpected response")
        if payload.get("code") not in (None, 0, 200):
            raise ExchangeError(f"Lighter API error {payload.get('code')} at {path}")
        return payload

    async def _load_metadata(self) -> dict[str, dict[str, Any]]:
        async with self._metadata_lock:
            if self._metadata:
                return self._metadata
            data = await self._get("/api/v1/orderBookDetails", {"filter": "perp"})
            rows = data.get("order_book_details")
            if not isinstance(rows, list):
                raise ExchangeError("Invalid Lighter market metadata")
            metadata = {}
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
                    raise ExchangeError("Invalid Lighter market metadata")
                if row.get("market_type") != "perp":
                    continue
                metadata[row["symbol"]] = row
            self._metadata = metadata
            self._by_id = {int(row["market_id"]): row for row in metadata.values()}
            return metadata

    async def _detail(self, market_id: int) -> dict[str, Any]:
        data = await self._get("/api/v1/orderBookDetails", {"market_id": market_id})
        rows = data.get("order_book_details")
        if not isinstance(rows, list):
            raise ExchangeError("Invalid Lighter market metadata")
        row = next(
            (
                item
                for item in rows
                if isinstance(item, dict) and int(item.get("market_id", -1)) == market_id
            ),
            None,
        )
        if row is None:
            raise ExchangeError("Unknown Lighter perpetual market")
        self._metadata[str(row["symbol"])] = row
        self._by_id[market_id] = row
        return row

    async def symbols(self) -> list[str]:
        return sorted(
            symbol
            for symbol, row in (await self._load_metadata()).items()
            if row.get("status") == "active" and not row.get("market_config", {}).get("hidden")
        )

    async def positions(self) -> Positions:
        if not self.account:
            return Positions([])
        metadata = await self._load_metadata()
        data = await self._get(
            "/api/v1/account",
            {
                "by": "index",
                "value": str(self.account.account_index),
                "active_only": "true",
            },
        )
        accounts = data.get("accounts")
        if not isinstance(accounts, list) or not accounts:
            raise ExchangeError("Lighter account was not found")
        rows = accounts[0].get("positions")
        if not isinstance(rows, list):
            raise ExchangeError("Invalid Lighter positions")
        try:
            return parse_positions(rows, metadata)
        except (KeyError, TypeError, ValueError) as exc:
            raise ExchangeError("Invalid Lighter positions") from exc

    async def market(self, symbol: str, interval: str) -> Market:
        if interval not in self.intervals:
            raise ExchangeError(f"{interval} is not available on Lighter")
        metadata = await self._load_metadata()
        symbol = self.normalize_symbol(symbol)
        if symbol not in metadata:
            raise ExchangeError("Unknown Lighter perpetual symbol")
        market_id = int(metadata[symbol]["market_id"])
        end = int(time.time() * 1000)
        seconds = INTERVALS[interval][1]
        bars, detail = await asyncio.gather(
            self._get(
                "/api/v1/candles",
                {
                    "market_id": market_id,
                    "resolution": interval,
                    "start_timestamp": end - seconds * 160_000,
                    "end_timestamp": end,
                    "count_back": 160,
                },
            ),
            self._detail(market_id),
        )
        try:
            rows = bars.get("c")
            if not isinstance(rows, list) or not rows:
                raise ValueError("Empty candles")
            parsed = {bar.time: bar for bar in (parse_candle(row, interval) for row in rows)}
            candles = sorted(parsed.values(), key=lambda bar: bar.time)[-160:]
            if not candles:
                raise ValueError("Empty candles")
            last = number(detail.get("last_trade_price") or candles[-1].close)
            mark = number(detail.get("mark_price") or last)
            if mark <= 0:
                raise ValueError("Invalid mark price")
            return Market(
                symbol,
                interval,
                candles,
                last,
                mark,
                number(detail.get("daily_price_change") or 0),
                number(detail["daily_price_high"]),
                number(detail["daily_price_low"]),
                number(detail.get("daily_quote_token_volume") or 0),
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ExchangeError("Invalid or empty Lighter market data") from exc

    async def history(
        self, symbol: str, interval: str, before: int, limit: int = 500
    ) -> list[Candle]:
        """Fetch candles strictly older than ``before`` (Unix seconds)."""
        if interval not in self.intervals:
            raise ExchangeError(f"{interval} is not available on Lighter")
        metadata = await self._load_metadata()
        symbol = self.normalize_symbol(symbol)
        if symbol not in metadata:
            raise ExchangeError("Unknown Lighter perpetual symbol")
        limit = max(1, min(2000, limit))
        seconds = INTERVALS[interval][1]
        data = await self._get(
            "/api/v1/candles",
            {
                "market_id": int(metadata[symbol]["market_id"]),
                "resolution": interval,
                "start_timestamp": (before - seconds * limit) * 1000,
                "end_timestamp": (before - 1) * 1000,
                "count_back": limit,
            },
        )
        try:
            rows = data.get("c")
            if not isinstance(rows, list):
                raise ValueError("Empty candles")
            parsed = {
                bar.time: bar
                for bar in (parse_candle(row, interval) for row in rows)
                if bar.time < before
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ExchangeError(f"Invalid candle history for {symbol}") from exc
        return sorted(parsed.values(), key=lambda bar: bar.time)[-limit:]

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
