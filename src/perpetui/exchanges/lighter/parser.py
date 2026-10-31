from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

from perpetui.domain.history import MAX_HISTORY
from perpetui.domain.intervals import INTERVAL_SECONDS
from perpetui.domain.models import (
    Candle,
    Market,
    Position,
    Positions,
    number,
)
from perpetui.exchanges.websocket import HistoryGap


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
    seconds = INTERVAL_SECONDS[interval]
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
