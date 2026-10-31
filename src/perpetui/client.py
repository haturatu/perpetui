"""Read-only MEXC Futures API with mexc-cli-compatible signing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from .credentials import Credentials
from .errors import MEXCError
from .i18n import _
from .models import (
    INTERVALS,
    Candle,
    Market,
    MarketEvent,
    Positions,
    PositionsEvent,
    normalize_symbol,
    number,
)
from .position_stream import PositionsStream, parse_position
from .stream import DEFAULT_WS_URL, MarketStream


class DataSource(Protocol):
    exchange: str
    default_symbol: str
    quantity_label: str
    public_hint: str
    authenticated: bool
    label: str
    streaming: bool
    position_streaming: bool
    intervals: tuple[str, ...]

    def normalize_symbol(self, value: str) -> str: ...

    async def positions(self) -> Positions: ...
    async def symbols(self) -> list[str]: ...
    async def market(self, symbol: str, interval: str) -> Market: ...
    def stream_market(self, seed: Market) -> AsyncIterator[MarketEvent]: ...
    def stream_positions(self) -> AsyncIterator[PositionsEvent]: ...
    async def close(self) -> None: ...


class MEXCClient:
    exchange = "MEXC"
    default_symbol = "BTC_USDT"
    quantity_label = "Contracts"
    public_hint = "Public market mode · configure mexc-cli or MEXC_API_KEY / MEXC_API_SECRET"
    intervals = tuple(INTERVALS)
    normalize_symbol = staticmethod(normalize_symbol)

    def __init__(
        self,
        credentials: Credentials | None = None,
        base_url: str = "https://api.mexc.com",
        timeout: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        websocket_url: str = DEFAULT_WS_URL,
        streaming: bool = True,
    ) -> None:
        self.credentials = credentials
        self.authenticated = credentials is not None
        self.label = credentials.label if credentials else _("public")
        self.streaming = streaming
        self.position_streaming = streaming and self.authenticated
        self.websocket_url = websocket_url
        self.timeout = timeout
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "perpetui/0.1.0"},
        )
        self._offset = 0
        self._synced = 0.0
        self._details: dict[str, dict[str, Any]] = {}
        self._details_lock = asyncio.Lock()

    def stream_market(self, seed: Market) -> AsyncIterator[MarketEvent]:
        return MarketStream(self.market, url=self.websocket_url, timeout=self.timeout).watch(seed)

    def stream_positions(self) -> AsyncIterator[PositionsEvent]:
        if not self.credentials:
            raise MEXCError("API credentials required for position updates")
        return PositionsStream(
            self.credentials,
            self.positions,
            self._contract_details,
            url=self.websocket_url,
            timeout=self.timeout,
        ).watch()

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(
        self, path: str, params: dict[str, str | int] | None = None, *, signed: bool = False
    ) -> Any:
        headers: dict[str, str] = {}
        query = urlencode(sorted((params or {}).items()))
        if signed:
            if not self.credentials:
                raise MEXCError("API credentials required for positions")
            if time.monotonic() - self._synced >= 60 or not self._synced:
                before = time.time() * 1000
                server_time = await self._get("/api/v1/contract/ping")
                try:
                    self._offset = int(server_time) - int((before + time.time() * 1000) / 2)
                except (ValueError, TypeError) as exc:
                    raise MEXCError("Invalid MEXC server time") from exc
                self._synced = time.monotonic()
            timestamp = str(int(time.time() * 1000) + self._offset)
            target = self.credentials.api_key + timestamp + query
            signature = hmac.new(
                self.credentials.api_secret.encode(), target.encode(), hashlib.sha256
            ).hexdigest()
            headers = {
                "ApiKey": self.credentials.api_key,
                "Request-Time": timestamp,
                "Signature": signature,
                "Recv-Window": "10",
                "Content-Type": "application/json",
            }
        try:
            response = await self._http.get(path, params=query, headers=headers)
        except httpx.RequestError as exc:
            raise MEXCError(f"Network error ({type(exc).__name__}); press r to retry") from exc
        if response.is_error:
            raise MEXCError(f"MEXC HTTP {response.status_code} at {path}")
        try:
            payload = json.loads(response.text, parse_float=Decimal)
        except (ValueError, TypeError) as exc:
            raise MEXCError("MEXC returned an invalid JSON response") from exc
        if not isinstance(payload, dict):
            raise MEXCError("MEXC returned an unexpected response")
        if payload.get("success") is False or payload.get("code") not in (None, 0, "0"):
            code = payload.get("code", "unknown")
            # Do not echo response bodies, headers or API keys into logs / the terminal.
            raise MEXCError(f"MEXC API error {code} at {path}; check API permissions and settings")
        if "data" not in payload:
            raise MEXCError("MEXC response is missing data")
        return payload["data"]

    async def market(self, symbol: str, interval: str) -> Market:
        exchange_interval, seconds = INTERVALS[interval]
        end = int(time.time())
        try:
            bars, ticker = await asyncio.gather(
                self._get(
                    f"/api/v1/contract/kline/{symbol}",
                    {
                        "interval": exchange_interval,
                        "start": end - seconds * 160,
                        "end": end,
                    },
                ),
                self._get("/api/v1/contract/ticker", {"symbol": symbol}),
            )
            if isinstance(ticker, list):
                ticker = next(row for row in ticker if row["symbol"] == symbol)
            if not isinstance(ticker, dict) or ticker.get("symbol") != symbol:
                raise ValueError("Mismatched ticker")
            columns = [bars[key] for key in ("time", "open", "high", "low", "close", "vol")]
            if not columns[0] or len({len(column) for column in columns}) != 1:
                raise ValueError("Empty or mismatched candle arrays")
            candles = []
            for timestamp, opening, high, low, closing, volume in zip(*columns, strict=True):
                candle = Candle(int(timestamp), *map(number, (opening, high, low, closing, volume)))
                if (
                    candle.low <= 0
                    or candle.volume < 0
                    or candle.high < max(candle.open, candle.close)
                    or candle.low > min(candle.open, candle.close)
                ):
                    raise ValueError("Invalid OHLC data")
                candles.append(candle)
            candles.sort(key=lambda bar: bar.time)
            return Market(
                symbol,
                interval,
                candles,
                number(ticker["lastPrice"]),
                number(ticker["fairPrice"]),
                number(ticker["riseFallRate"]) * 100,
                number(ticker["high24Price"]),
                number(ticker["lower24Price"]),
                number(ticker["amount24"]),
            )
        except (KeyError, ValueError, TypeError, StopIteration) as exc:
            raise MEXCError(f"Invalid or empty market data for {symbol}") from exc

    async def _contract_details(self) -> dict[str, dict[str, Any]]:
        async with self._details_lock:
            if not self._details:
                data = await self._get("/api/v1/contract/detail")
                if not isinstance(data, list) or any(
                    not isinstance(row, dict) or not isinstance(row.get("symbol"), str)
                    for row in data
                ):
                    raise MEXCError("Invalid contract metadata from MEXC")
                self._details = {row["symbol"]: row for row in data}
        return self._details

    async def symbols(self) -> list[str]:
        details = await self._contract_details()
        symbols = []
        for symbol, info in details.items():
            if info.get("state", 0) not in (0, "0"):
                continue
            try:
                if normalize_symbol(symbol) == symbol:
                    symbols.append(symbol)
            except ValueError:
                continue
        return sorted(symbols)

    async def positions(self) -> Positions:
        if not self.authenticated:
            return Positions([])
        data = await self._get("/api/v1/private/position/open_positions", signed=True)
        try:
            if not isinstance(data, list):
                raise ValueError("Positions must be an array")
            positions = [
                parse_position(row)
                for row in data
                if number(row["holdVol"]) > 0 and int(row.get("state", 1)) in (1, 2)
            ]
        except (KeyError, ValueError, TypeError) as exc:
            raise MEXCError("Invalid positions data from MEXC") from exc
        if not positions:
            return Positions([])
        # Fetch all quotes and metadata once, rather than two requests per position.
        quotes, details = await asyncio.gather(
            self._get("/api/v1/contract/ticker"),
            self._contract_details(),
            return_exceptions=True,
        )
        tickers = (
            {row["symbol"]: row for row in quotes if isinstance(row, dict) and "symbol" in row}
            if isinstance(quotes, list)
            else {}
        )
        metadata = details if isinstance(details, dict) else {}
        enriched, missing = [], []
        for position in positions:
            try:
                detail = metadata[position.symbol]
                enriched.append(
                    replace(
                        position,
                        mark=number(tickers[position.symbol]["fairPrice"]),
                        contract_size=number(detail["contractSize"]),
                        settlement=str(detail.get("settleCoin", position.settlement)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                enriched.append(position)
                missing.append(position.symbol)
        warnings = (
            (_("Mark / PnL unavailable: {symbols}", symbols=", ".join(dict.fromkeys(missing))),)
            if missing
            else ()
        )
        return Positions(enriched, warnings)
