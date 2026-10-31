"""Validated exchange data; monetary calculations stay in Decimal."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("Min1", 60),
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "30m": ("Min30", 1800),
    "1h": ("Min60", 3600),
    "4h": ("Hour4", 14400),
    "1d": ("Day1", 86400),
    "1w": ("Week1", 604800),
}


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().replace("/", "_").replace("-", "_")
    if "_" not in symbol:
        for quote in ("USDT", "USDC", "USD"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                symbol = f"{symbol[: -len(quote)]}_{quote}"
                break
        else:
            symbol = f"{symbol}_USDT"
    if not re.fullmatch(r"[A-Z0-9]+_(USDT|USDC|USD)", symbol):
        raise ValueError("Use a symbol such as BTC, ETHUSDT or BTC_USDT")
    return symbol


def number(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric data from MEXC") from exc
    if not result.is_finite():
        raise ValueError("Non-finite numeric data from MEXC")
    return result


def price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    magnitude = abs(value)
    places = (
        2 if magnitude >= 100 else 4 if magnitude >= 1 else 6 if magnitude >= Decimal(".01") else 8
    )
    return f"{value:,.{places}f}"


def compact(value: Decimal) -> str:
    for suffix, divisor in (("B", 10**9), ("M", 10**6), ("K", 10**3)):
        if abs(value) >= divisor:
            return f"{value / divisor:.2f}{suffix}"
    return f"{value:,.2f}"


@dataclass(frozen=True)
class Candle:
    time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Position:
    id: str
    symbol: str
    side: str
    contracts: Decimal
    entry: Decimal
    liquidation: Decimal
    leverage: int
    margin: Decimal
    margin_mode: str
    mark: Decimal | None = None
    contract_size: Decimal | None = None
    settlement: str = "USDT"
    updated_at: int = 0
    version: int = 0
    reported_pnl: Decimal | None = None
    reported_roe: Decimal | None = None

    @property
    def base_size(self) -> Decimal | None:
        if self.contract_size is None:
            return None
        return self.contracts * self.contract_size

    @property
    def pnl(self) -> Decimal | None:
        if self.reported_pnl is not None:
            return self.reported_pnl
        # Inverse contracts require a different formula; never display a linear estimate.
        if self.mark is None or self.base_size is None or self.settlement not in {"USDT", "USDC"}:
            return None
        direction = 1 if self.side == "LONG" else -1
        return (self.mark - self.entry) * self.base_size * direction

    @property
    def roe(self) -> Decimal | None:
        if self.reported_roe is not None:
            return self.reported_roe
        pnl = self.pnl
        return pnl / self.margin * 100 if pnl is not None and self.margin > 0 else None


@dataclass(frozen=True)
class Positions:
    items: list[Position]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Market:
    symbol: str
    interval: str
    candles: list[Candle]
    last: Decimal
    mark: Decimal
    change: Decimal
    high: Decimal | None
    low: Decimal | None
    amount: Decimal


@dataclass(frozen=True)
class MarketEvent:
    state: Literal["connecting", "live", "reconnecting"]
    market: Market | None = None


@dataclass(frozen=True)
class PositionsEvent:
    state: Literal["connecting", "live", "reconnecting"]
    positions: Positions | None = None
