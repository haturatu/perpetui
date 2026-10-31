"""Read-only Hyperliquid perpetuals, including builder-deployed (HIP-3) markets."""

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
from .hl_account import HLAccount
from .i18n import _
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

MAINNET_URL = "https://api.hyperliquid.xyz"
TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
logger = logging.getLogger("perpetui")


def normalize_hl_symbol(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[/_](USDC|USD)$", "", value, flags=re.IGNORECASE)
    if not re.fullmatch(r"(?:[a-zA-Z0-9]+:)?[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", value):
        raise ValueError("Use a Hyperliquid symbol such as BTC, HYPE or xyz:CL")
    prefix, sep, coin = value.rpartition(":")
    coin = "k" + coin[1:].upper() if coin.lower().startswith("k") else coin.upper()
    return prefix.lower() + sep + coin


def parse_candle(row: dict[str, Any], symbol: str, interval: str) -> Candle:
    if row.get("s") != symbol or row.get("i") != interval:
        raise ValueError("Mismatched Hyperliquid candle")
    bar = Candle(int(row["t"]) // 1000, *(number(row[k]) for k in ("o", "h", "l", "c", "v")))
    if (
        bar.low <= 0
        or bar.volume < 0
        or bar.high < max(bar.open, bar.close)
        or bar.low > min(bar.open, bar.close)
    ):
        raise ValueError("Invalid Hyperliquid candle")
    return bar


def parse_positions(
    states: dict[str, dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
) -> Positions:
    positions = []
    for dex, state in states.items():
        rows = state["assetPositions"]
        if not isinstance(rows, list):
            raise ValueError("Invalid Hyperliquid positions")
        for row in rows:
            data = row["position"]
            size = number(data["szi"])
            if size == 0:
                continue
            coin = str(data["coin"])
            if dex and ":" not in coin:
                coin = dex + ":" + coin
            entry, value = number(data["entryPx"]), number(data["positionValue"])
            if entry <= 0 or value <= 0:
                raise ValueError("Invalid Hyperliquid position price")
            positions.append(
                Position(
                    id=coin,
                    symbol=coin,
                    side="LONG" if size > 0 else "SHORT",
                    contracts=abs(size),
                    entry=entry,
                    liquidation=number(data.get("liquidationPx") or 0),
                    leverage=int(data["leverage"]["value"]),
                    margin=number(data["marginUsed"]),
                    margin_mode="Cross" if data["leverage"]["type"] == "cross" else "Isolated",
                    mark=value / abs(size),
                    contract_size=Decimal(1),
                    settlement=metadata.get(coin, {}).get("quote", "USD"),
                    updated_at=int(state.get("time", 0)),
                    reported_pnl=number(data["unrealizedPnl"]),
                    reported_roe=number(data["returnOnEquity"]) * 100,
                )
            )
    return Positions(positions)


def apply_market(market: Market, message: dict[str, Any]) -> Market:
    channel, data = message.get("channel"), message.get("data")
    if channel == "activeAssetCtx":
        if not isinstance(data, dict) or data.get("coin") != market.symbol:
            return market
        ctx = data["ctx"]
        mark, previous = number(ctx["markPx"]), number(ctx["prevDayPx"])
        if mark <= 0:
            raise ValueError("Invalid Hyperliquid mark price")
        return replace(
            market,
            mark=mark,
            amount=number(ctx["dayNtlVlm"]),
            change=(mark / previous - 1) * 100 if previous else Decimal(0),
        )
    if channel != "candle":
        return market
    rows = data if isinstance(data, list) else [data]
    bars = list(market.candles)
    for row in sorted(rows, key=lambda row: int(row["t"])):
        if row.get("s") != market.symbol or row.get("i") != market.interval:
            continue
        candle = parse_candle(row, market.symbol, market.interval)
        if bars and candle.time < bars[-1].time:
            continue
        if bars and candle.time > bars[-1].time + INTERVALS[market.interval][1]:
            raise HistoryGap("Hyperliquid candle history needs repair")
        if bars and candle.time == bars[-1].time:
            if candle.volume < bars[-1].volume:
                continue
            bars[-1] = candle
        else:
            bars.append(candle)
    return (
        replace(market, candles=bars[-MAX_HISTORY:], last=bars[-1].close)
        if bars != market.candles
        else market
    )


class HyperliquidClient:
    exchange = "Hyperliquid"
    default_symbol = "BTC"
    quantity_label = "Size"
    public_hint = "Public market mode · configure hl or HYPERLIQUID_WALLET_ADDRESS"
    intervals = tuple(INTERVALS)

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
                    "startTime": end - INTERVALS[interval][1] * 160_000,
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
        seconds = INTERVALS[interval][1]
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
