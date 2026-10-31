"""Bounded history caches and coalesced asynchronous backfill per client."""

from __future__ import annotations

import asyncio

from perpetui.domain.history import CandleStore
from perpetui.domain.models import Candle
from perpetui.exchanges.base import DataSource


class HistoryService:
    def __init__(self, client: DataSource) -> None:
        self.client = client
        self.cache: dict[tuple[str, str], CandleStore] = {}
        self._pending: dict[tuple[str, str, int], asyncio.Task[list[Candle]]] = {}

    async def older(self, symbol: str, interval: str, before: int) -> list[Candle]:
        key = (symbol, interval, before)
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self.client.history(symbol, interval, before))
            self._pending[key] = task

            def finished(done: asyncio.Task[list[Candle]]) -> None:
                if self._pending.get(key) is done:
                    self._pending.pop(key)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
        bars = await asyncio.shield(task)
        store = self.cache.setdefault((symbol, interval), CandleStore())
        store.merge_live(bars)
        return [bar for bar in store.all() if bar.time < before]

    async def close(self) -> None:
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self.cache.clear()
