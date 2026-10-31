import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest
from textual.widgets import DataTable, Input, Static

from perpetui.app import TradingApp
from perpetui.chart import CandleChart
from perpetui.client import MEXCError
from perpetui.demo import DemoClient
from perpetui.models import MarketEvent


async def settle(app, pilot):
    while list(app.workers):
        await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.parametrize("empty,expected", [(False, "ETH_USDT"), (True, "BTC_USDT")])
async def test_initial_chart_uses_first_position_or_btc(empty, expected):
    app = TradingApp(DemoClient(empty), refresh_seconds=3600)
    async with app.run_test(size=(140, 44)) as pilot:
        await settle(app, pilot)
        assert app.symbol == expected
        assert app.market.symbol == expected
        assert app.query_one(CandleChart).market.symbol == expected
        assert app.query_one(DataTable).row_count == (0 if empty else 3)
        assert app.query_one("#positions-pane").display
        assert app.query_one("#empty").display == empty


async def test_select_position_timeframe_search_and_refresh_preserve_selection():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(140, 44)) as pilot:
        await settle(app, pilot)
        await pilot.press("down")
        await settle(app, pilot)
        assert app.symbol == "BTC_USDT"
        assert app.market.symbol == "BTC_USDT"
        await pilot.press("]")
        await settle(app, pilot)
        assert app.interval == "30m"
        assert app.market.interval == "30m"
        await pilot.press("r")
        await settle(app, pilot)
        assert app.symbol == "BTC_USDT"
        assert app.query_one(DataTable).cursor_row == 1
        await pilot.press("/")
        assert app.query_one(Input).has_focus
        app.query_one(Input).value = "sol"
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.symbol == "SOL_USDT"
        assert app.market.symbol == "SOL_USDT"


async def test_explicit_symbol_overrides_first_position():
    app = TradingApp(DemoClient(), symbol="SOL_USDT", refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.symbol == app.market.symbol == "SOL_USDT"


async def test_small_terminal_and_empty_chart_are_renderable():
    app = TradingApp(DemoClient(True), refresh_seconds=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert app.screen.has_class("compact")
        assert "LOCAL" in app.query_one(CandleChart).render().plain
        assert app.query_one("#status").region.bottom <= app.size.height


class FailingClient(DemoClient):
    fail = False
    closed = False

    async def positions(self):
        if self.fail:
            raise MEXCError("test position failure")
        return await super().positions()

    async def market(self, symbol, interval):
        if self.fail:
            raise MEXCError("test market failure")
        return await super().market(symbol, interval)

    async def close(self):
        self.closed = True


async def test_failed_refresh_retains_data_and_recovers():
    client = FailingClient()
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        previous = app.market
        client.fail = True
        await pilot.press("r")
        await settle(app, pilot)
        assert app.market is previous
        assert len(app.positions) == 3
        assert app.query_one(CandleChart).market is previous
        assert app.query_one("#status").has_class("error")
        assert "STALE" in str(app.query_one("#status", Static).render())
        client.fail = False
        await pilot.press("r")
        await settle(app, pilot)
        assert not app.query_one("#status").has_class("error")
    assert client.closed


async def test_first_position_failure_shows_btc_then_selects_first_on_recovery():
    client = FailingClient()
    client.fail = True
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert app.symbol == "BTC_USDT"
        assert app.query_one("#status").has_class("error")
        client.fail = False
        app.refresh_data()
        await settle(app, pilot)
        assert app.market.symbol == app.symbol == "ETH_USDT"
        assert len(app.positions) == 3
        assert not app.query_one("#status").has_class("error")


async def test_periodic_refresh_updates_without_changing_selected_chart():
    class CountingClient(DemoClient):
        position_calls = 0
        market_calls = 0

        async def positions(self):
            self.position_calls += 1
            return await super().positions()

        async def market(self, symbol, interval):
            self.market_calls += 1
            return await super().market(symbol, interval)

    client = CountingClient()
    app = TradingApp(client, refresh_seconds=0.25)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.pause(0.6)
        assert client.position_calls >= 2
        assert client.market_calls >= 2
        assert app.symbol == "ETH_USDT"


class SlowClient(DemoClient):
    async def market(self, symbol, interval):
        if symbol == "BTC_USDT":
            await asyncio.sleep(0.2)
        return await super().market(symbol, interval)


async def test_fast_switch_cancels_obsolete_chart_request():
    app = TradingApp(SlowClient(), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.symbol = "BTC_USDT"
        app.load_market()
        await asyncio.sleep(0.01)
        app.symbol = "SOL_USDT"
        latest = app.load_market()
        await latest.wait()
        await pilot.pause()
        assert app.market.symbol == "SOL_USDT"
        assert app.query_one(CandleChart).market.symbol == "SOL_USDT"


class StreamingDemo(DemoClient):
    streaming = True

    def __init__(self):
        super().__init__()
        self.queue = asyncio.Queue()
        self.active = set()
        self.stopped = []
        self.market_calls = 0
        self.position_calls = 0

    async def market(self, symbol, interval):
        self.market_calls += 1
        return await super().market(symbol, interval)

    async def positions(self):
        self.position_calls += 1
        return await super().positions()

    async def stream_market(self, seed):
        key = (seed.symbol, seed.interval)
        self.active.add(key)
        try:
            yield MarketEvent("connecting")
            while True:
                yield await self.queue.get()
        finally:
            self.active.remove(key)
            self.stopped.append(key)


async def test_live_stream_updates_chart_without_polling_and_stops_on_selection_and_exit():
    client = StreamingDemo()
    app = TradingApp(client, refresh_seconds=0.25)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        update = replace(app.market, last=Decimal("4000"))
        await client.queue.put(MarketEvent("live", update))
        await pilot.pause()
        initial_calls = client.market_calls
        await pilot.pause(0.6)
        assert app.market is update
        assert app.query_one(CandleChart).market is update
        assert app._stream_state == "live"
        assert "WS" not in str(app.query_one("#status", Static).render())
        assert client.market_calls == initial_calls
        assert client.position_calls >= 2
        await pilot.press("down")
        await settle(app, pilot)
        assert client.active == {("BTC_USDT", "15m")}
        assert ("ETH_USDT", "15m") in client.stopped
        await pilot.press("]")
        await settle(app, pilot)
        assert client.active == {("BTC_USDT", "30m")}
        assert ("BTC_USDT", "15m") in client.stopped
    assert not client.active
    assert app._stream_task is None


async def test_reconnecting_uses_rest_fallback_without_restarting_the_socket():
    client = StreamingDemo()
    app = TradingApp(client, refresh_seconds=0.25)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        stream_task = app._stream_task
        await client.queue.put(MarketEvent("reconnecting"))
        await pilot.pause(0.6)
        assert client.market_calls >= 2
        assert app._stream_task is stream_task
        assert app._stream_state == "reconnecting"
        assert "REST" not in str(app.query_one("#status", Static).render())
        assert app.market is not None
        assert not client.stopped


async def test_public_mode_loads_initial_history_once():
    class PublicDemo(StreamingDemo):
        authenticated = False

        async def positions(self):
            from perpetui.models import Positions

            return Positions([])

        async def market(self, symbol, interval):
            await asyncio.sleep(0.05)
            return await super().market(symbol, interval)

    client = PublicDemo()
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert client.market_calls == 1
        assert app.market.symbol == "BTC_USDT"


@pytest.mark.parametrize("fail", [False, True])
async def test_delayed_rest_fallback_does_not_overwrite_recovered_stream(fail):
    class DelayedFallback(StreamingDemo):
        block = False

        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.started = asyncio.Event()

        async def market(self, symbol, interval):
            if self.block:
                self.started.set()
                await self.release.wait()
                if fail:
                    raise MEXCError("delayed REST error")
            return await super().market(symbol, interval)

    client = DelayedFallback()
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        client.block = True
        worker = app.load_market(restart_stream=False)
        await client.started.wait()
        update = replace(app.market, last=Decimal("4321.23"))
        await client.queue.put(MarketEvent("live", update))
        await pilot.pause()
        client.release.set()
        await worker.wait()
        assert app.market is update
        assert app.query_one(CandleChart).market is update
        assert not app.query_one("#status").has_class("error")
