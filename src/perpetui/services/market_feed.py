"""Share snapshots and streams by symbol/timeframe within one exchange client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from dataclasses import dataclass, field

from perpetui.domain.models import Market, MarketEvent
from perpetui.exchanges.base import DataSource


@dataclass(eq=False)
class _Feed:
    latest: Market
    state: str = "connecting"
    listeners: set[asyncio.Queue[MarketEvent | None]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    error: Exception | None = None


class MarketFeedHub:
    """A client-scoped hub; the last subscriber closes the underlying stream."""

    def __init__(self, client: DataSource) -> None:
        self.client = client
        self._feeds: dict[tuple[str, str], _Feed] = {}
        self._loads: dict[tuple[str, str], asyncio.Task[Market]] = {}
        self._closed = False

    async def snapshot(self, symbol: str, interval: str, *, refresh: bool = False) -> Market:
        if self._closed:
            raise RuntimeError("Market feed hub is closed")
        key = (symbol, interval)
        feed = self._feeds.get(key)
        if not refresh and feed and feed.task and not feed.task.done():
            return feed.latest
        task = self._loads.get(key)
        if task is None:
            task = asyncio.create_task(self.client.market(symbol, interval))
            self._loads[key] = task

            def finished(done: asyncio.Task[Market]) -> None:
                if self._loads.get(key) is done:
                    self._loads.pop(key)
                if not done.cancelled():
                    done.exception()  # Retrieve errors even if every waiter was cancelled.

            task.add_done_callback(finished)
        # One pane changing symbol must not cancel another pane's shared request.
        return await asyncio.shield(task)

    @staticmethod
    def _send(feed: _Feed, event: MarketEvent | None) -> None:
        for queue in tuple(feed.listeners):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    async def _run(self, feed: _Feed) -> None:
        try:
            async with aclosing(self.client.stream_market(feed.latest)) as stream:
                async for event in stream:
                    feed.state = event.state
                    if event.market is not None:
                        feed.latest = event.market
                    self._send(feed, event)
        except Exception as exc:
            feed.error = exc
        finally:
            self._send(feed, None)

    async def subscribe(self, seed: Market) -> AsyncIterator[MarketEvent]:
        if self._closed:
            return
        key = (seed.symbol, seed.interval)
        queue: asyncio.Queue[MarketEvent | None] = asyncio.Queue(maxsize=2)
        feed = self._feeds.get(key)
        if feed is None or (feed.task is not None and feed.task.done()):
            feed = self._feeds[key] = _Feed(seed)
            feed.listeners.add(queue)
            feed.task = asyncio.create_task(self._run(feed))
        else:
            feed.listeners.add(queue)
            queue.put_nowait(MarketEvent(feed.state, feed.latest))
        try:
            while (event := await queue.get()) is not None:
                yield event
            if feed.error is not None:
                raise feed.error
        finally:
            feed.listeners.discard(queue)
            if not feed.listeners:
                if self._feeds.get(key) is feed:
                    self._feeds.pop(key)
                if feed.task is not None:
                    feed.task.cancel()
                    with suppress(asyncio.CancelledError):
                        await feed.task

    async def close(self) -> None:
        self._closed = True
        tasks = [feed.task for feed in self._feeds.values() if feed.task is not None]
        tasks.extend(self._loads.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._feeds.clear()
        self._loads.clear()
