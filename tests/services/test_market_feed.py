import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from perpetui.domain.models import MarketEvent
from perpetui.exchanges.demo import DemoClient
from perpetui.services.chart_session import ChartSession
from perpetui.services.market_feed import MarketFeedHub


class FeedSource(DemoClient):
    streaming = True

    def __init__(self):
        super().__init__()
        self.loads = 0
        self.starts = 0
        self.stops = 0
        self.queues = {}
        self.release = asyncio.Event()
        self.release.set()
        self.loading = asyncio.Event()

    async def market(self, symbol, interval):
        self.loads += 1
        self.loading.set()
        await self.release.wait()
        return await super().market(symbol, interval)

    async def stream_market(self, seed):
        self.starts += 1
        key = seed.symbol, seed.interval
        queue = self.queues[key] = asyncio.Queue()
        try:
            yield MarketEvent("live", seed)
            while True:
                yield await queue.get()
        finally:
            self.stops += 1
            self.queues.pop(key)


async def wait_live(session):
    if session.state.connection == "live":
        return
    ready = asyncio.Event()
    previous = session.on_change

    def changed():
        previous()
        if session.state.connection == "live":
            ready.set()

    session.on_change = changed
    try:
        await asyncio.wait_for(ready.wait(), 2)
    finally:
        session.on_change = previous


async def test_subscribers_share_updates_and_only_the_last_one_closes_the_feed():
    source = FeedSource()
    hub = MarketFeedHub(source)
    seed = await hub.snapshot("BTC_USDT", "15m")
    first, second = hub.subscribe(seed), hub.subscribe(seed)
    try:
        assert (await anext(first)).market is seed
        assert (await anext(second)).market is seed
        assert source.starts == 1
        await first.aclose()
        assert source.stops == 0
        updated = replace(seed, last=Decimal("123"))
        await source.queues[seed.symbol, seed.interval].put(MarketEvent("live", updated))
        assert (await anext(second)).market is updated
        assert await hub.snapshot(seed.symbol, seed.interval) is updated
        assert source.loads == 1
    finally:
        await first.aclose()
        await second.aclose()
        await hub.close()
    assert source.stops == 1 and not source.queues


async def test_cancelling_one_shared_snapshot_does_not_cancel_the_other_chart():
    source = FeedSource()
    source.release.clear()
    hub = MarketFeedHub(source)
    first = ChartSession(hub, 1, "BTC_USDT", "15m")
    second = ChartSession(hub, 2, "BTC_USDT", "15m")
    pending = asyncio.create_task(first.load())
    other = asyncio.create_task(second.load())
    await source.loading.wait()
    first.configure(symbol="SOL_USDT")
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    source.release.set()
    await other
    await wait_live(second)
    assert first.state.symbol == "SOL_USDT" and first.state.market is None
    assert second.state.market.symbol == "BTC_USDT"
    assert source.loads == 1 and source.starts == 1
    await first.stop()
    await second.stop()
    await hub.close()
    assert source.stops == 1


async def test_two_sessions_share_one_feed_but_keep_independent_selection_and_lifecycle():
    source = FeedSource()
    hub = MarketFeedHub(source)
    sessions = [ChartSession(hub, index, "BTC_USDT", "15m") for index in (1, 2)]
    try:
        await asyncio.gather(*(session.load() for session in sessions))
        await asyncio.gather(*(wait_live(session) for session in sessions))
        assert source.loads == 1 and source.starts == 1
        sessions[0].configure(interval="30m")
        await sessions[0].load()
        await wait_live(sessions[0])
        assert sessions[1].state.interval == "15m"
        assert source.stops == 0
        await sessions[0].stop()
        assert source.stops == 1
        assert sessions[1].state.connection == "live"
    finally:
        await asyncio.gather(*(session.stop() for session in sessions))
        await hub.close()
    assert source.stops == 2


async def test_hub_shutdown_cancels_inflight_snapshots_and_rejects_new_work():
    source = FeedSource()
    source.release.clear()
    hub = MarketFeedHub(source)
    pending = asyncio.create_task(hub.snapshot("BTC_USDT", "15m"))
    await source.loading.wait()
    await hub.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with pytest.raises(RuntimeError, match="closed"):
        await hub.snapshot("BTC_USDT", "15m")


async def test_a_finite_feed_delivers_its_last_snapshot_before_ending():
    class Finite(FeedSource):
        async def stream_market(self, seed):
            yield MarketEvent("live", seed)

    hub = MarketFeedHub(Finite())
    seed = await hub.snapshot("BTC_USDT", "15m")
    assert [event.market async for event in hub.subscribe(seed)] == [seed]
    await hub.close()


async def test_hub_shutdown_wakes_stream_subscribers_and_joins_the_transport():
    source = FeedSource()
    hub = MarketFeedHub(source)
    seed = await hub.snapshot("BTC_USDT", "15m")
    events = hub.subscribe(seed)
    assert (await anext(events)).market is seed
    pending = asyncio.create_task(anext(events))
    await hub.close()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, 2)
    assert source.stops == 1 and not source.queues
