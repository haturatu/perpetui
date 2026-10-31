import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest
from textual.widgets import DataTable, Input, Select, Static

from perpetui.app import TradingApp
from perpetui.chart import CandleChart
from perpetui.chart_panel import ChartFrame, ExtraChart
from perpetui.credentials import CredentialsError
from perpetui.demo import DemoClient
from perpetui.i18n import install_language
from perpetui.models import MarketEvent, PositionsEvent
from perpetui.search import SymbolSearch


class LiveSource(DemoClient):
    streaming = True
    position_streaming = True

    def __init__(self, exchange="mexc", empty=False):
        super().__init__(empty, exchange=exchange)
        self.connections = {}
        self.closed = False

    async def stream_market(self, seed):
        queue = asyncio.Queue()
        token = object()
        self.connections[token] = (seed.symbol, seed.interval, queue)
        try:
            yield MarketEvent("live", seed)
            while True:
                yield await queue.get()
        finally:
            self.connections.pop(token)

    async def stream_positions(self):
        token = object()
        self.connections[token] = ("positions", "", None)
        try:
            yield PositionsEvent("live", await self.positions())
            await asyncio.Event().wait()
        finally:
            self.connections.pop(token)

    async def close(self):
        assert not self.connections
        self.closed = True

    async def emit(self, market):
        for symbol, interval, queue in list(self.connections.values()):
            if (symbol, interval) == (market.symbol, market.interval):
                await queue.put(MarketEvent("live", market))


class PublicSource(LiveSource):
    authenticated = False
    position_streaming = False

    async def positions(self):
        pytest.fail("Public mode requested positions")


async def settle(app, pilot):
    while list(app.workers):
        await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.parametrize("exchange", ["mexc", "hyperliquid"])
@pytest.mark.parametrize("language", ["en", "ja"])
async def test_public_mode_uses_position_space_for_charts_and_keeps_controls_working(
    exchange, language
):
    install_language(language)
    try:
        source = PublicSource(exchange)
        app = TradingApp(source, refresh_seconds=3600)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(app, pilot)
            assert not app.query_one("#positions-pane").display
            assert app.query_one("#positions-pane").region.height == 0
            assert app.query_one("#chart-grid").region.bottom == app.query_one("#status").region.y
            assert app.focused is app.query_one("#chart-pane")
            assert app.market.symbol == source.default_symbol
            assert len(source.connections) == 1
            status = str(app.query_one("#status", Static).render())
            assert "Positions" not in status and "ポジション" not in status
            await pilot.press("ctrl+2")
            await settle(app, pilot)
            assert len(source.connections) == 2
            await pilot.click("#chart-pane-2")
            await pilot.press("/", "s", "o", "l", "enter")
            await settle(app, pilot)
            second = app.query_one("#chart-pane-2", ExtraChart)
            assert second.market.symbol == source.normalize_symbol("SOL")
            assert app.focused is second
            await pilot.press("escape", "r")
            app.refresh_data()
            await settle(app, pilot)
            assert app.focused is second
            assert second.market.symbol == source.normalize_symbol("SOL")
        assert source.closed
    finally:
        install_language("en")


async def test_switch_to_public_hides_positions_and_switch_back_restores_them():
    source = LiveSource()
    created = []

    def factory(exchange):
        target = PublicSource(exchange) if exchange == "hyperliquid" else LiveSource(exchange)
        created.append(target)
        return target

    app = TradingApp(source, client_factory=factory, refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        original_height = app.query_one("#chart-grid").region.height
        await pilot.press("ctrl+2", "e", "down", "enter")
        await settle(app, pilot)
        assert source.closed
        assert not app.query_one("#positions-pane").display
        assert app.query_one("#chart-grid").region.height > original_height
        assert app.market.symbol == "BTC" and app.positions == []
        assert len(created[0].connections) == 2
        assert "Positions" not in str(app.query_one("#status", Static).render())
        await pilot.press("e", "up", "enter")
        await settle(app, pilot)
        assert created[0].closed
        assert app.query_one("#positions-pane").display
        assert app.query_one("#chart-grid").region.height == original_height
        assert app.market.symbol == "ETH_USDT" and len(app.positions) == 3
        assert len(created[1].connections) == 3
        assert "Positions" in str(app.query_one("#status", Static).render())
    assert created[1].closed


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_exchange_selector_replaces_units_symbols_and_closes_old_connections(size):
    initial = LiveSource()
    created = []

    def factory(exchange):
        source = LiveSource(exchange)
        created.append(source)
        return source

    app = TradingApp(initial, client_factory=factory, refresh_seconds=3600)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        assert app.query_one("#exchange").region.height == 1
        await pilot.press("e", "down", "enter")
        await settle(app, pilot)
        assert initial.closed and not initial.connections
        assert app.client is created[0]
        assert app.symbol == app.market.symbol == "ETH"
        assert list(app.query_one(DataTable).columns.values())[2].label.plain == "Size"
        assert "xyz:CL" in app.query_one(SymbolSearch).symbols
        assert app._stream_state == app._positions_stream_state == "live"
        await pilot.press("e", "up", "enter")
        await settle(app, pilot)
        assert created[0].closed
        assert app.symbol == "ETH_USDT"
        assert list(app.query_one(DataTable).columns.values())[2].label.plain == "Contracts"
    assert all(source.closed for source in created)


async def test_configuration_failure_retains_live_exchange_and_reverts_selector():
    source = LiveSource()

    def factory(exchange):
        raise CredentialsError("Cannot read hl account database")

    app = TradingApp(source, client_factory=factory, refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        market = app.market
        await pilot.press("e", "down", "enter")
        await settle(app, pilot)
        assert app.client is source and app.market is market
        assert not source.closed and len(source.connections) == 2
        assert app.query_one("#exchange", Select).value == "mexc"
        assert not app.query_one("#toolbar").disabled
        assert not app._switching


async def test_switch_ignores_obsolete_requests_even_if_the_transport_swallows_cancellation():
    class SlowSource(LiveSource):
        def __init__(self):
            super().__init__()
            self.started = [asyncio.Event() for _ in range(3)]

        async def delayed(self, index):
            self.started[index].set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass

        async def market(self, symbol, interval):
            await self.delayed(0)
            return await super().market(symbol, interval)

        async def positions(self):
            await self.delayed(1)
            return await super().positions()

        async def symbols(self):
            await self.delayed(2)
            return await super().symbols()

    source = SlowSource()
    app = TradingApp(
        source,
        symbol="BTC_USDT",
        client_factory=lambda venue: LiveSource(venue),
        refresh_seconds=3600,
    )
    async with app.run_test() as pilot:
        await asyncio.gather(*(event.wait() for event in source.started))
        await pilot.press("e", "down", "enter")
        await settle(app, pilot)
        assert source.closed
        assert app.symbol == app.market.symbol == "ETH"
        assert all("_USDT" not in p.symbol for p in app.positions)
        assert "BTC_USDT" not in app.query_one(SymbolSearch).symbols


async def test_chart_count_shortcuts_focus_and_per_pane_symbol_timeframe_and_zoom():
    source = LiveSource()
    app = TradingApp(source, refresh_seconds=3600)
    async with app.run_test(size=(160, 50)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+4")
        await settle(app, pilot)
        assert len(app.query(ChartFrame)) == 4
        assert len(source.connections) == 5  # Four markets and one portfolio.
        second = app.query_one("#chart-pane-2", ExtraChart)
        assert second.symbol == "BTC_USDT"
        await pilot.click("#chart-pane-2")
        await pilot.press("/", "s", "o", "l", "enter")
        await settle(app, pilot)
        assert second.symbol == second.market.symbol == "SOL_USDT"
        assert app.symbol == "ETH_USDT"
        await pilot.press("]", "plus")
        await settle(app, pilot)
        assert second.interval == "30m" and app.interval == "15m"
        assert second.query_one(CandleChart).price_zoom == 2
        assert app.query_one("#chart", CandleChart).price_zoom == 1
        await source.emit(replace(app.market, last=Decimal(999)))
        await pilot.pause()
        assert "SOL" in str(app.query_one("#market-summary", Static).render())
        await pilot.click("#chart-pane")
        assert app._active_extra is None
        assert app.query_one("#timeframe", Select).value == "15m"
        await pilot.press("ctrl+1")
        await settle(app, pilot)
        assert len(app.query(ChartFrame)) == 1 and len(source.connections) == 2
    assert source.closed


async def test_rapid_count_changes_and_removing_selected_panes_do_not_leak_connections():
    source = LiveSource()
    app = TradingApp(source, refresh_seconds=3600)
    async with app.run_test(size=(180, 65)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+9")
        await settle(app, pilot)
        assert len(app.query(ChartFrame)) == 9 and len(source.connections) == 10
        await pilot.click("#chart-pane-9")
        await pilot.press("ctrl+2", "ctrl+4", "ctrl+1")
        await settle(app, pilot)
        assert len(app.query(ChartFrame)) == 1
        assert app._active_extra is None
        assert len(source.connections) == 2


async def test_exchange_switch_rebuilds_all_panes_and_preserves_count_and_timeframe():
    source = LiveSource()
    targets = []

    def factory(venue):
        target = LiveSource(venue)
        targets.append(target)
        return target

    app = TradingApp(source, client_factory=factory, refresh_seconds=3600)
    async with app.run_test(size=(160, 50)) as pilot:
        await settle(app, pilot)
        await pilot.press("]", "ctrl+4")
        await settle(app, pilot)
        await pilot.click("#chart-pane-2")
        await pilot.press("]", "plus", "v")
        await settle(app, pilot)
        await pilot.press("e", "down", "enter")
        await settle(app, pilot)
        assert source.closed and not source.connections
        assert app.client.exchange == "Hyperliquid"
        assert len(app.query(ChartFrame)) == 4 and app.interval == "30m"
        assert app.symbol == "ETH"
        assert all(
            panel.market and "_USDT" not in panel.market.symbol for panel in app.query(ExtraChart)
        )
        assert len(targets[0].connections) == 5
        second = app.query_one("#chart-pane-2", ExtraChart)
        assert second.interval == "1h" and second.query_one(CandleChart).price_zoom == 2
        assert not second.query_one(CandleChart).detail
    assert targets[0].closed


async def test_ui_hides_transport_details_while_live_and_reconnecting():
    source = LiveSource()
    app = TradingApp(source, refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        for state in ("live", "reconnecting"):
            app._stream_state = app._positions_stream_state = state
            app._status()
            visible = (
                str(app.query_one("#status", Static).render())
                + app.query_one("#positions-pane").border_title
            )
            assert "WS" not in visible and "REST" not in visible
            assert "Chart" in visible and "Positions" in visible


async def test_count_shortcut_works_while_typing_and_header_selector_stays_in_sync():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("/", "e", "t", "h", "ctrl+2")
        await settle(app, pilot)
        assert app.query_one(Input).value == "eth"
        assert len(app.query(ChartFrame)) == 2
        assert app.query_one("#chart-count", Select).value == 2
        await pilot.click("#chart-count")
        await pilot.press("down", "enter")
        await settle(app, pilot)
        assert len(app.query(ChartFrame)) == 3
        assert app.query_one("#chart-count", Select).value == 3


async def test_delayed_initial_positions_do_not_replace_a_manually_chosen_extra_symbol():
    class SlowPositions(DemoClient):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()

        async def positions(self):
            await self.release.wait()
            return await super().positions()

    source = SlowPositions()
    app = TradingApp(source, symbol="BTC_USDT", refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("ctrl+2")
        await pilot.pause()
        await pilot.click("#chart-pane-2")
        await pilot.press("/", "z", "e", "c", "enter")
        await pilot.pause()
        panel = app.query_one("#chart-pane-2", ExtraChart)
        assert panel.symbol == "ZEC_USDT"
        source.release.set()
        await settle(app, pilot)
        assert panel.symbol == panel.market.symbol == "ZEC_USDT"


async def test_number_keys_select_charts_without_stealing_search_typing():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(160, 50)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+3")
        await settle(app, pilot)
        await pilot.press("2")
        await pilot.pause()
        assert app._active_extra is app.query_one("#chart-pane-2", ExtraChart)
        assert app.focused is app.query_one("#chart-pane-2")
        await pilot.press("/", "1", "2")
        assert app.query_one(Input).value == "12"
        assert app._active_extra is app.query_one("#chart-pane-2", ExtraChart)


async def test_cursor_navigation_enters_history_backfills_and_returns_live():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        chart = app.query_one(CandleChart)
        initial_earliest = chart.earliest_time()
        await pilot.press("1", "left")
        await pilot.pause()
        assert chart.cursor_time is not None
        assert chart.mode_label().startswith("HISTORY")
        assert "HISTORY" in app.query_one("#chart-pane").border_title
        assert "HISTORY" in str(app.query_one("#candle-info", Static).render())
        original_earliest = chart.earliest_time()
        await pilot.press("home")
        await settle(app, pilot)
        assert chart.cursor_time == original_earliest
        assert chart.earliest_time() < initial_earliest
        await pilot.press("end")
        await pilot.pause()
        assert chart.cursor_time is None
        assert "LIVE" in app.query_one("#chart-pane").border_title


async def test_all_chart_sync_cursor_sync_maximize_positions_and_help():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(160, 50)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+3", "1")
        await settle(app, pilot)
        assert app.interval == "15m"
        await pilot.press("]", "a")
        await settle(app, pilot)
        assert app._all_timeframes and app.interval == "30m"
        assert [panel.interval for panel in app.query(ExtraChart)] == ["30m", "30m"]
        await pilot.press("]")
        await pilot.pause(0.25)
        await settle(app, pilot)
        assert app.interval == "1h"
        assert [panel.interval for panel in app.query(ExtraChart)] == ["1h", "1h"]

        await pilot.press("2", "left", "s")
        await pilot.pause()
        assert app._cursor_sync
        charts = [panel.query_one(CandleChart) for panel in app.query(ExtraChart)]
        assert all(chart.cursor_time is not None for chart in charts)
        assert charts[0].cursor_time == charts[1].cursor_time

        active = app._active_extra
        await pilot.press("z")
        await pilot.pause()
        assert app._maximized
        assert active.display and not app.query_one("#chart-pane").display
        await pilot.press("z")
        await pilot.pause()
        assert not app._maximized and app.query_one("#chart-pane").display

        await pilot.press("p")
        await pilot.pause()
        assert not app.query_one("#positions-pane").display
        await pilot.press("p")
        await pilot.pause()
        assert app.query_one("#positions-pane").display

        await pilot.press("question_mark")
        await pilot.pause()
        assert type(app.screen).__name__ == "HelpScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert type(app.screen).__name__ != "HelpScreen"


async def test_enter_applies_the_already_highlighted_position_to_the_selected_pane():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+2")
        await settle(app, pilot)
        panel = app.query_one("#chart-pane-2", ExtraChart)
        assert panel.symbol == "BTC_USDT"
        await pilot.click("#chart-pane-2")
        await pilot.press("escape", "enter")
        await settle(app, pilot)
        assert panel.symbol == panel.market.symbol == "ETH_USDT"
        assert app.symbol == "ETH_USDT"
