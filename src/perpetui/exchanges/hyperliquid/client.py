"""Read-only Hyperliquid perpetuals, including builder-deployed (HIP-3) markets."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal
from typing import Any

import httpx

from perpetui.domain.intervals import INTERVAL_SECONDS
from perpetui.domain.models import (
    Candle,
    Market,
    Positions,
    number,
)
from perpetui.errors import ExchangeError
from perpetui.exchanges.hyperliquid.account import HLAccount
from perpetui.exchanges.hyperliquid.parser import (
    normalize_hl_symbol,
    parse_candle,
    parse_positions,
)
from perpetui.exchanges.hyperliquid.stream import HyperliquidClientStreams
from perpetui.i18n import _

MAINNET_URL = "https://api.hyperliquid.xyz"
TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
logger = logging.getLogger("perpetui")


class HyperliquidClient(HyperliquidClientStreams):
    exchange = "Hyperliquid"
    default_symbol = "BTC"
    quantity_label = "Size"
    public_hint = "Public market mode · configure hl or HYPERLIQUID_WALLET_ADDRESS"
    intervals = tuple(INTERVAL_SECONDS)

    def __init__(
        self,
        account: HLAccount | None = None,
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
            or base_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
        )
        self.timeout = timeout
        self.heartbeat, self.stale_after, self.retry_delay = heartbeat, stale_after, retry_delay
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._metadata: dict[str, dict[str, Any]] = {}
        self._dexs: list[str] = []
        self._metadata_lock = asyncio.Lock()
        self._request_limit = asyncio.Semaphore(4)
        self._states: dict[str, dict[str, Any]] = {}

    def normalize_symbol(self, value: str) -> str:
        symbol = normalize_hl_symbol(value)
        return next((coin for coin in self._metadata if coin.lower() == symbol.lower()), symbol)

    async def close(self) -> None:
        await self._http.aclose()

    async def _info(self, kind: str, **kwargs: Any) -> Any:
        try:
            async with self._request_limit:
                response = await self._http.post("/info", json={"type": kind, **kwargs})
        except httpx.RequestError as exc:
            raise ExchangeError(f"Hyperliquid network error ({type(exc).__name__})") from exc
        if response.is_error:
            raise ExchangeError(f"Hyperliquid HTTP {response.status_code} ({kind})")
        try:
            data = json.loads(response.text, parse_float=Decimal)
        except (ValueError, TypeError) as exc:
            raise ExchangeError("Invalid Hyperliquid JSON response") from exc
        if not isinstance(data, (dict, list)) or (isinstance(data, dict) and "error" in data):
            raise ExchangeError(f"Hyperliquid request rejected ({kind})")
        return data

    async def _load_metadata(self) -> dict[str, dict[str, Any]]:
        async with self._metadata_lock:
            if self._metadata:
                return self._metadata
            metas, dexs, spot = await asyncio.gather(
                self._info("allPerpMetas"),
                self._info("perpDexs"),
                self._info("spotMeta"),
            )
            try:
                if not isinstance(metas, list) or len(metas) != len(dexs):
                    raise ValueError("Mismatched DEX metadata")
                tokens = {int(token["index"]): token["name"] for token in spot["tokens"]}
                metadata, names = {}, []
                for meta, dex in zip(metas, dexs, strict=True):
                    name = dex["name"] if dex else ""
                    names.append(name)
                    quote = tokens.get(int(meta.get("collateralToken", 0)), "USD")
                    for asset in meta["universe"]:
                        metadata[asset["name"]] = {**asset, "dex": name, "quote": quote}
                self._metadata, self._dexs = metadata, names
            except (KeyError, TypeError, ValueError) as exc:
                raise ExchangeError("Invalid Hyperliquid market metadata") from exc
        return self._metadata

    async def symbols(self) -> list[str]:
        return sorted(
            coin
            for coin, data in (await self._load_metadata()).items()
            if not data.get("isDelisted")
        )

    async def positions(self) -> Positions:
        if not self.account:
            return Positions([])
        await self._load_metadata()
        states = await asyncio.gather(
            *(
                self._info("clearinghouseState", user=self.account.address, dex=dex)
                for dex in self._dexs
            )
        )
        try:
            book = dict(zip(self._dexs, states, strict=True))
            snapshot = parse_positions(book, self._metadata)
        except (KeyError, TypeError, ValueError) as exc:
            raise ExchangeError("Invalid Hyperliquid positions") from exc
        self._states = book
        return snapshot

    async def market(self, symbol: str, interval: str) -> Market:
        await self._load_metadata()
        symbol = self.normalize_symbol(symbol)
        if symbol not in self._metadata:
            raise ExchangeError("Unknown Hyperliquid perpetual symbol")
        end = int(time.time() * 1000)
        bars, context = await asyncio.gather(
            self._info(
                "candleSnapshot",
                req={
                    "coin": symbol,
                    "interval": interval,
                    "startTime": end - INTERVAL_SECONDS[interval] * 160_000,
                    "endTime": end,
                },
            ),
            self._info("metaAndAssetCtxs", dex=self._metadata[symbol]["dex"]),
        )
        try:
            parsed = {
                bar.time: bar for bar in (parse_candle(row, symbol, interval) for row in bars)
            }
            candles = sorted(parsed.values(), key=lambda bar: bar.time)[-160:]
            if not candles:
                raise ValueError("Empty candles")
            ctxs = {
                asset["name"]: ctx
                for asset, ctx in zip(context[0]["universe"], context[1], strict=True)
            }
            ctx = ctxs[symbol]
            mark, previous = number(ctx["markPx"]), number(ctx["prevDayPx"])
            if mark <= 0:
                raise ValueError("Invalid mark price")
            return Market(
                symbol,
                interval,
                candles,
                candles[-1].close,
                mark,
                (mark / previous - 1) * 100 if previous else Decimal(0),
                None,
                None,
                number(ctx["dayNtlVlm"]),
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ExchangeError("Invalid or empty Hyperliquid market data") from exc

    async def history(
        self, symbol: str, interval: str, before: int, limit: int = 500
    ) -> list[Candle]:
        """Fetch candles strictly older than ``before`` (Unix seconds)."""
        await self._load_metadata()
        symbol = self.normalize_symbol(symbol)
        if symbol not in self._metadata:
            raise ExchangeError("Unknown Hyperliquid perpetual symbol")
        limit = max(1, min(5000, limit))
        seconds = INTERVAL_SECONDS[interval]
        bars = await self._info(
            "candleSnapshot",
            req={
                "coin": symbol,
                "interval": interval,
                "startTime": (before - seconds * limit) * 1000,
                "endTime": (before - 1) * 1000,
            },
        )
        try:
            parsed = {
                bar.time: bar
                for bar in (
                    parse_candle(row, symbol, interval)
                    for row in bars
                    if int(row.get("t", 0)) // 1000 < before
                )
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ExchangeError(f"Invalid candle history for {symbol}") from exc
        return sorted(parsed.values(), key=lambda bar: bar.time)[-limit:]
