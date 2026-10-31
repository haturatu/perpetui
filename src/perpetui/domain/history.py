"""Timestamp-keyed candle history shared by charts and stream merges."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from perpetui.domain.models import Candle

MAX_HISTORY = 5000


@dataclass
class CandleStore:
    """Keep a bounded, de-duplicated window of candles for one market."""

    limit: int = MAX_HISTORY

    def __post_init__(self) -> None:
        self._candles: list[Candle] = []
        self.version = 0

    def reset(self, candles: list[Candle]) -> None:
        self._candles = self._merge([], candles)
        self.version += 1

    def all(self) -> list[Candle]:
        return list(self._candles)

    def merge_live(self, candles: list[Candle]) -> bool:
        merged = self._merge(self._candles, candles)
        if merged == self._candles:
            return False
        self._candles = merged
        self.version += 1
        return True

    def prepend(self, candles: list[Candle]) -> bool:
        if not candles:
            return False
        before = self._candles[0].time if self._candles else None
        merged = self._merge(self._candles, candles)
        if merged == self._candles:
            return False
        self._candles = merged
        self.version += 1
        return before is None or merged[0].time < before

    def earliest_time(self) -> int | None:
        return self._candles[0].time if self._candles else None

    def latest_time(self) -> int | None:
        return self._candles[-1].time if self._candles else None

    def index_at_or_before(self, timestamp: int) -> int | None:
        if not self._candles:
            return None
        index = bisect_right([bar.time for bar in self._candles], timestamp) - 1
        return max(0, index)

    def nearest(self, timestamp: int) -> Candle | None:
        index = self.index_at_or_before(timestamp)
        return self._candles[index] if index is not None else None

    def _merge(self, current: list[Candle], incoming: list[Candle]) -> list[Candle]:
        merged = {bar.time: bar for bar in current}
        for bar in incoming:
            merged[bar.time] = bar
        return sorted(merged.values(), key=lambda bar: bar.time)[-self.limit :]
