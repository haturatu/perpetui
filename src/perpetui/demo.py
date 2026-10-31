"""Deterministic sample data; demo mode never accesses the network or credentials."""

from __future__ import annotations

import math
import random
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal

from .hyperliquid import normalize_hl_symbol
from .i18n import _
from .lighter import LIGHTER_INTERVALS, normalize_lighter_symbol
from .models import (
    INTERVALS,
    Candle,
    Market,
    MarketEvent,
    Position,
    Positions,
    PositionsEvent,
    normalize_symbol,
)


class DemoClient:
    exchange = "MEXC"
    default_symbol = "BTC_USDT"
    quantity_label = "Contracts"
    public_hint = "Public market mode · configure mexc-cli or MEXC_API_KEY / MEXC_API_SECRET"
    authenticated = True
    label = "DEMO · sample data"
    streaming = False
    position_streaming = False
    intervals = tuple(INTERVALS)

    async def stream_market(self, seed: Market) -> AsyncIterator[MarketEvent]:
        yield MarketEvent("live", seed)

    async def stream_positions(self) -> AsyncIterator[PositionsEvent]:
        yield PositionsEvent("live", await self.positions())

    def __init__(self, empty: bool = False, *, exchange: str = "mexc") -> None:
        self.empty = empty
        self.label = _("DEMO · sample data")
        if exchange in {"hl", "hyperliquid"}:
            self.exchange, self.default_symbol, self.quantity_label = "Hyperliquid", "BTC", "Size"
            self.public_hint = "Public market mode · configure hl or HYPERLIQUID_WALLET_ADDRESS"
        elif exchange in {"lt", "lighter"}:
            self.exchange, self.default_symbol, self.quantity_label = "Lighter", "BTC", "Size"
            self.public_hint = "Public market mode · configure lighter or LIGHTER_ACCOUNT_INDEX"
            self.intervals = LIGHTER_INTERVALS

    def normalize_symbol(self, value: str) -> str:
        if self.exchange == "Hyperliquid":
            return normalize_hl_symbol(value)
        if self.exchange == "Lighter":
            return normalize_lighter_symbol(value)
        return normalize_symbol(value)

    async def close(self) -> None:
        pass

    async def symbols(self) -> list[str]:
        if self.exchange == "Hyperliquid":
            return ["BTC", "ETH", "SOL", "HYPE", "ZEC", "xyz:CL", "xyz:TSLA", "kPEPE"]
        if self.exchange == "Lighter":
            return ["BTC", "ETH", "SOL", "HYPE", "ZEC", "1000PEPE", "TAO", "AAVE"]
        return [
            "BTC_USDT",
            "ETH_USDT",
            "SOL_USDT",
            "XRP_USDT",
            "ZEC_USDT",
            "ZEN_USDT",
            "ZETA_USDT",
            "ZEC_USDC",
            "USOIL_USDT",
            "UKOIL_USDT",
        ]

    async def positions(self) -> Positions:
        if self.empty:
            return Positions([])
        snapshot = Positions(
            [
                Position(
                    "1",
                    "ETH_USDT",
                    "LONG",
                    Decimal(120),
                    Decimal("3472.80"),
                    Decimal("3218.20"),
                    10,
                    Decimal("416.736"),
                    "Isolated",
                    Decimal("3518.42"),
                    Decimal(".01"),
                ),
                Position(
                    "2",
                    "BTC_USDT",
                    "SHORT",
                    Decimal(35),
                    Decimal("97840"),
                    Decimal("104120"),
                    5,
                    Decimal("684.88"),
                    "Cross",
                    Decimal("97428.60"),
                    Decimal(".001"),
                ),
                Position(
                    "3",
                    "SOL_USDT",
                    "LONG",
                    Decimal(80),
                    Decimal("178.25"),
                    Decimal("163.40"),
                    10,
                    Decimal("142.60"),
                    "Isolated",
                    Decimal("176.82"),
                    Decimal(".1"),
                ),
            ]
        )
        if self.exchange in {"Hyperliquid", "Lighter"}:
            return Positions(
                [
                    replace(
                        p,
                        id=p.symbol.split("_")[0],
                        symbol=p.symbol.split("_")[0],
                        contracts=p.base_size,
                        contract_size=Decimal(1),
                        settlement="USDC",
                    )
                    for p in snapshot.items
                ]
            )
        return snapshot

    async def market(self, symbol: str, interval: str) -> Market:
        target = {"BTC": 97428.60, "ETH": 3518.42, "SOL": 176.82}.get(symbol.split("_")[0], 42.5)
        rng = random.Random(symbol + interval)
        seconds = INTERVALS[interval][1]
        now = int(time.time()) // seconds * seconds
        values = []
        previous = target * 0.982
        for i in range(160):
            closing = previous + target * (rng.uniform(-0.0022, 0.0026) + math.sin(i / 9) * 0.0004)
            high = max(previous, closing) + target * rng.uniform(0.0002, 0.0013)
            low = min(previous, closing) - target * rng.uniform(0.0002, 0.0013)
            values.append((previous, high, low, closing, rng.uniform(100, 2400)))
            previous = closing
        factor = target / values[-1][3]
        candles = [
            Candle(
                now - (159 - i) * seconds,
                *(Decimal(str(round(value * factor, 8))) for value in bar[:4]),
                Decimal(str(round(bar[4], 2))),
            )
            for i, bar in enumerate(values)
        ]
        last = Decimal(str(target))
        return Market(
            symbol,
            interval,
            candles,
            last,
            last,
            Decimal("1.84"),
            last * Decimal("1.012"),
            last * Decimal(".967"),
            Decimal("284901352.71"),
        )

    async def history(
        self, symbol: str, interval: str, before: int, limit: int = 500
    ) -> list[Candle]:
        market = await self.market(symbol, interval)
        seconds = INTERVALS[interval][1]
        rng = random.Random(symbol + interval + "history")
        anchor = float(market.candles[0].open)
        candles = []
        for index in range(min(limit, 2000)):
            timestamp = before - (min(limit, 2000) - index) * seconds
            opening = anchor * (1 + rng.uniform(-0.012, 0.012))
            closing = opening * (1 + rng.uniform(-0.002, 0.002))
            high = max(opening, closing) * (1 + rng.uniform(0.0002, 0.0012))
            low = min(opening, closing) * (1 - rng.uniform(0.0002, 0.0012))
            candles.append(
                Candle(
                    timestamp,
                    *(Decimal(str(round(value, 8))) for value in (opening, high, low, closing)),
                    Decimal(str(round(rng.uniform(100, 2400), 2))),
                )
            )
        return candles
