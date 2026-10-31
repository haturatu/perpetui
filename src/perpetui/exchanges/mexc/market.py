from __future__ import annotations

import asyncio
import re
import time

from perpetui.domain.models import Candle, Market, number
from perpetui.errors import MEXCError
from perpetui.exchanges.mexc.intervals import MEXC_INTERVALS


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


class MEXCMarket:
    async def market(self, symbol: str, interval: str) -> Market:
        exchange_interval, seconds = MEXC_INTERVALS[interval]
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

    async def history(
        self, symbol: str, interval: str, before: int, limit: int = 500
    ) -> list[Candle]:
        """Fetch candles strictly older than ``before`` (Unix seconds)."""
        exchange_interval, seconds = MEXC_INTERVALS[interval]
        limit = max(1, min(2000, limit))
        end = before - 1
        try:
            bars = await self._get(
                f"/api/v1/contract/kline/{symbol}",
                {
                    "interval": exchange_interval,
                    "start": end - seconds * limit,
                    "end": end,
                },
            )
            columns = [bars[key] for key in ("time", "open", "high", "low", "close", "vol")]
            if not columns[0] or len({len(column) for column in columns}) != 1:
                return []
            candles = [
                Candle(int(timestamp), *map(number, (opening, high, low, closing, volume)))
                for timestamp, opening, high, low, closing, volume in zip(*columns, strict=True)
            ]
        except (KeyError, ValueError, TypeError) as exc:
            raise MEXCError(f"Invalid candle history for {symbol}") from exc
        candles.sort(key=lambda bar: bar.time)
        return [bar for bar in candles if bar.time < before][-limit:]
