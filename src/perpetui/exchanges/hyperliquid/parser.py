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
        if bars and candle.time > bars[-1].time + INTERVAL_SECONDS[market.interval]:
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
