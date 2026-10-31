import asyncio

from perpetui.exchanges.demo import DemoClient
from perpetui.services.history import HistoryService


async def test_shared_backfill_is_not_cancelled_when_one_chart_changes_symbol():
    class Source(DemoClient):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def history(self, *args):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return await super().history(*args)

    source = Source()
    service = HistoryService(source)
    before = 1_800_000_000
    first = asyncio.create_task(service.older("BTC_USDT", "15m", before))
    second = asyncio.create_task(service.older("BTC_USDT", "15m", before))
    await source.started.wait()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    source.release.set()
    bars = await second
    assert source.calls == 1
    assert bars and all(bar.time < before for bar in bars)
    assert len({bar.time for bar in bars}) == len(bars)
    assert service.cache["BTC_USDT", "15m"].all() == bars
    await service.close()
    assert not service.cache
