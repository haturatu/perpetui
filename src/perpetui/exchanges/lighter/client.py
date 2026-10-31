"""Read-only Lighter perpetuals over the public REST and WebSocket APIs."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal
from typing import Any

import httpx

from perpetui import __version__
from perpetui.domain.intervals import INTERVAL_SECONDS
from perpetui.domain.models import (
    Candle,
    Market,
    Positions,
    number,
)
from perpetui.errors import ExchangeError
from perpetui.exchanges.lighter.account import LighterAccount
from perpetui.exchanges.lighter.parser import (
    normalize_lighter_symbol,
    parse_candle,
    parse_positions,
)
from perpetui.exchanges.lighter.stream import LighterClientStreams
from perpetui.i18n import _

MAINNET_URL = "https://mainnet.zklighter.elliot.ai"
TESTNET_URL = "https://testnet.zklighter.elliot.ai"
LIGHTER_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
logger = logging.getLogger("perpetui")


class LighterClient(LighterClientStreams):
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
            headers={"User-Agent": f"perpetui/{__version__}"},
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
        seconds = INTERVAL_SECONDS[interval]
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
        seconds = INTERVAL_SECONDS[interval]
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
