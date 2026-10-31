import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest
from textual.widgets import DataTable, Static

from perpetui.app import TradingApp
from perpetui.chart import CandleChart
from perpetui.client import MEXCError
from perpetui.demo import DemoClient
from perpetui.models import Positions, PositionsEvent, price


class StreamingPositions(DemoClient):
    position_streaming = True

    def __init__(self, empty=False):
        super().__init__(empty)
        self.queue = asyncio.Queue()
        self.position_calls = 0
        self.active = 0
        self.stopped = 0

    async def positions(self):
        self.position_calls += 1
        return await super().positions()

    async def stream_positions(self):
        self.active += 1
        try:
            yield PositionsEvent("live", await super().positions())
            while True:
                yield await self.queue.get()
        finally:
            self.active -= 1
            self.stopped += 1


async def settle(app, pilot):
    while list(app.workers):
        await app.workers.wait_for_complete()
    await pilot.pause()


async def test_live_positions_revalue_all_rows_and_totals_without_rest_or_cursor_reset():
    client = StreamingPositions()
    app = TradingApp(client, refresh_seconds=0.15)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert app._positions_stream_state == "live"
        await pilot.press("down")
        await settle(app, pilot)
        table = app.query_one(DataTable)
        table.scroll_to(x=40, animate=False)
        await pilot.pause()
        scroll_x = table.scroll_x
        previous_calls = client.position_calls
        snapshot = Positions([replace(p, mark=p.mark + 10) for p in app.positions])
        await client.queue.put(PositionsEvent("live", snapshot))
        await pilot.pause(0.35)
        assert app.positions == snapshot.items
        assert client.position_calls == previous_calls
        assert app.symbol == app.market.symbol == "BTC_USDT"
        assert table.cursor_row == 1
        assert table.scroll_x == scroll_x
        assert table.get_row("1")[4] == price(snapshot.items[0].mark)
        assert table.get_row("2")[4] == price(snapshot.items[1].mark)
        assert table.get_row("3")[4] == price(snapshot.items[2].mark)
        total = sum(p.pnl for p in snapshot.items)
        assert f"{total:+,.2f}" in str(app.query_one("#positions-summary", Static).render())
        assert app.query_one("#positions-pane").border_title == " OPEN POSITIONS "
    assert client.active == 0 and client.stopped == 1
    assert app._positions_stream_task is None


async def test_new_resized_and_closed_positions_update_rows_and_chart_entry():
    client = StreamingPositions(empty=True)
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert app.symbol == "BTC_USDT"
        position = (await DemoClient().positions()).items[1]
        await client.queue.put(PositionsEvent("live", Positions([position])))
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 1 and table.display
        assert not app.query_one("#empty").display
        assert app.query_one(CandleChart).entry == position.entry
        updated = replace(position, contracts=Decimal(7), entry=Decimal(98000))
        await client.queue.put(PositionsEvent("live", Positions([updated])))
        await pilot.pause()
        assert table.get_row(position.id)[2] == "7"
        assert app.query_one(CandleChart).entry == 98000
        await client.queue.put(PositionsEvent("live", Positions([])))
        await pilot.pause()
        assert table.row_count == 0
        assert app.query_one("#empty").display
        assert app.query_one(CandleChart).entry is None
        assert app._selected_id is None


async def test_chart_switches_leave_position_stream_running_and_manual_refresh_restarts_it():
    client = StreamingPositions()
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        task = app._positions_stream_task
        await pilot.press("down", "]")
        await settle(app, pilot)
        assert app._positions_stream_task is task
        assert client.active == 1 and client.stopped == 0
        await pilot.press("r")
        await settle(app, pilot)
        assert app._positions_stream_task is not task
        assert client.active == 1 and client.stopped == 1
        assert app.symbol == "BTC_USDT"
    assert client.stopped == 2


async def test_reconnecting_positions_poll_rest_without_restarting_stream():
    client = StreamingPositions()
    app = TradingApp(client, refresh_seconds=0.1)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        task = app._positions_stream_task
        initial_calls = client.position_calls
        await client.queue.put(PositionsEvent("reconnecting"))
        await pilot.pause(0.35)
        assert client.position_calls > initial_calls
        assert app._positions_stream_task is task
        assert app._positions_stream_state == "reconnecting"
        snapshot = Positions([replace(app.positions[0], mark=Decimal(4000))])
        await client.queue.put(PositionsEvent("live", snapshot))
        await pilot.pause()
        assert app.positions == snapshot.items
        assert app._positions_stream_state == "live"
        assert app.query_one("#positions-pane").border_title == " OPEN POSITIONS "


@pytest.mark.parametrize("fail", [False, True])
async def test_late_fallback_cannot_overwrite_recovered_position_stream(fail):
    class DelayedPositions(StreamingPositions):
        block = False

        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def positions(self):
            if self.block:
                self.started.set()
                await self.release.wait()
                if fail:
                    raise MEXCError("delayed failure")
            return await super().positions()

    client = DelayedPositions()
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await client.queue.put(PositionsEvent("reconnecting"))
        await pilot.pause()
        client.block = True
        worker = app.load_positions()
        await client.started.wait()
        update = Positions([replace(app.positions[0], mark=Decimal(4000))])
        await client.queue.put(PositionsEvent("live", update))
        await pilot.pause()
        client.release.set()
        await worker.wait()
        assert app.positions == update.items
        assert not app._positions_error
        assert not app.query_one("#status").has_class("error")
