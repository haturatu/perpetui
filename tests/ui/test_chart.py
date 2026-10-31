from dataclasses import replace
from decimal import Decimal

import pytest
from plotext._settings import defaults
from rich.cells import cell_len
from rich.console import Console
from textual.app import App, ComposeResult

from perpetui.domain.models import Candle
from perpetui.exchanges.demo import DemoClient
from perpetui.ui.app import TradingApp
from perpetui.ui.formatting import price
from perpetui.ui.widgets.chart import CandleChart
from perpetui.ui.widgets.chart_plot import build_chart


@pytest.mark.parametrize("size", [(30, 6), (35, 6), (74, 7), (114, 17), (154, 30)])
async def test_plotext_chart_fits_widget_with_volume_and_time_labels(size):
    market = await DemoClient().market("ETH_USDT", "15m")
    rendered = build_chart(market, Decimal("3472.80"), *size)
    lines = rendered.plain.splitlines()
    assert len(lines) <= size[1]
    assert max(map(cell_len, lines)) <= size[0]
    assert "VOL" in rendered.plain
    assert "LOCAL" in rendered.plain
    assert "\x1b" not in rendered.plain


@pytest.mark.parametrize("detail", [False, True])
async def test_chart_preserves_theme_entry_and_restores_library_candle_defaults(detail):
    market = await DemoClient().market("ETH_USDT", "15m")
    before = defaults.candlestick_up_color, defaults.candlestick_down_color
    rendered = build_chart(market, Decimal("3472.80"), 114, 17, detail=detail)
    assert "E 3,472.80" in rendered.plain
    assert price(market.last) in rendered.plain
    colors = {
        span.style.color.get_truecolor() for span in rendered.spans if span.style.color is not None
    }
    assert (45, 212, 191) in colors  # bullish
    assert (251, 113, 133) in colors  # bearish
    assert (231, 189, 104) in colors  # entry
    backgrounds = {
        span.style.bgcolor.get_truecolor()
        for span in rendered.spans
        if span.style.bgcolor is not None
    }
    assert backgrounds == (
        {(8, 14, 24)} if detail else {(8, 14, 24), (45, 212, 191), (251, 113, 133)}
    )
    assert (defaults.candlestick_up_color, defaults.candlestick_down_color) == before
    assert build_chart(market, Decimal("90000"), 114, 17, detail=detail).plain == (
        build_chart(market, None, 114, 17, detail=detail).plain
    )


@pytest.mark.parametrize("value", ["100", "0.00000001"])
async def test_single_flat_candle_and_zero_volume_are_renderable(value):
    market = await DemoClient().market("BTC_USDT", "15m")
    amount = Decimal(value)
    candle = Candle(market.candles[-1].time, amount, amount, amount, amount, Decimal(0))
    flat = replace(market, candles=[candle], last=amount)
    rendered = build_chart(flat, amount, 74, 7)
    assert "VOL" in rendered.plain and "LOCAL" in rendered.plain
    assert "E " in rendered.plain
    assert len(rendered.plain.splitlines()) <= 7


async def test_two_chart_widgets_keep_their_own_data_across_resize_and_updates():
    eth, btc = (
        await DemoClient().market("ETH_USDT", "15m"),
        await DemoClient().market("BTC_USDT", "1d"),
    )

    class ChartApp(App):
        def compose(self) -> ComposeResult:
            yield CandleChart(id="eth")
            yield CandleChart(id="btc")

    app = ChartApp()
    async with app.run_test(size=(120, 40)) as pilot:
        first, second = app.query_one("#eth", CandleChart), app.query_one("#btc", CandleChart)
        first.set_market(eth)
        second.set_market(btc)
        await pilot.pause()
        initial = first.render().plain
        assert price(eth.last) in initial
        assert price(btc.last) in second.render().plain
        assert first.render().plain == initial
        visible = first.bars_visible
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert first.bars_visible < visible
        assert max(map(cell_len, first.render().plain.splitlines())) <= first.content_size.width
        updated = replace(eth, last=eth.last + Decimal(".25"))
        first.set_market(updated)
        await pilot.pause()
        assert price(updated.last) in first.render().plain
        assert price(btc.last) in second.render().plain


@pytest.mark.parametrize("width", [35, 74, 114])
async def test_half_cell_bodies_and_wicks_share_the_same_columns(width):
    market = await DemoClient().market("BTC_USDT", "15m")
    bars = [
        Candle(
            market.candles[-1].time + i * 900,
            Decimal(22),
            Decimal(90),
            Decimal(10),
            Decimal(78),
            Decimal(0),
        )
        for i in range(7)
    ]
    market = replace(market, candles=bars, last=Decimal(80))
    frame = build_chart(market, None, width, 17, detail=False)
    body_columns, wick_columns = set(), set()
    console = Console()
    lines = frame.plain.splitlines()
    offset = 0
    for line in lines[:12]:
        for column, character in enumerate(line):
            style = frame.get_style_at_offset(console, offset + column)
            if style.bgcolor and style.bgcolor.get_truecolor() == (45, 212, 191):
                body_columns.add(column)
            if character == "│" and style.color and style.color.get_truecolor() == (45, 212, 191):
                wick_columns.add(column)
        offset += len(line) + 1
    assert len(body_columns) == 7
    assert body_columns == wick_columns
    assert any(char in "▀▄" for line in lines[:12] for char in line)


async def test_price_zoom_reveals_small_movements_without_changing_market_data():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        while list(app.workers):
            await app.workers.wait_for_complete()
        await pilot.pause()
        chart = app.query_one(CandleChart)
        market = app.market
        unzoomed = chart.render().plain
        await pilot.press("plus", "plus")
        await pilot.pause()
        assert chart.price_zoom == 4
        assert "PRICE ×4" in app.query_one("#chart-pane").border_title
        assert chart.render().plain != unzoomed
        assert app.market is market
        await pilot.press("minus")
        assert chart.price_zoom == 2
        await pilot.press("0")
        await pilot.pause()
        assert chart.price_zoom == 1
        assert chart.render().plain == unzoomed


@pytest.mark.parametrize("zoom", [2, 4, 8, 16, 64])
@pytest.mark.parametrize("detail", [False, True])
async def test_price_zoom_clips_offscreen_candles_and_keeps_source_ohlc(zoom, detail):
    market = await DemoClient().market("ETH_USDT", "15m")
    original = list(market.candles)
    output = build_chart(market, None, 114, 17, zoom, detail=detail)
    assert "LOCAL" in output.plain
    assert len(output.plain.splitlines()) <= 17
    assert market.candles == original


async def test_close_changes_within_one_character_row_still_change_candle_body():
    market = await DemoClient().market("BTC_USDT", "15m")
    candle = Candle(
        market.candles[-1].time, Decimal(40), Decimal(100), Decimal(1), Decimal(51), Decimal(0)
    )
    first = replace(market, candles=[candle], last=Decimal(51))
    second = replace(first, candles=[replace(candle, close=Decimal(57))])
    # Both closes land on the same whole-character row at this scale. Keep ticker
    # price fixed so only the candle body can account for a changed price panel.
    before = build_chart(first, None, 74, 17).plain.splitlines()[:12]
    after = build_chart(second, None, 74, 17).plain.splitlines()[:12]
    assert before != after


@pytest.mark.parametrize("part", ["body", "wick"])
async def test_small_movements_previously_quantized_away_reach_detailed_candles(part):
    market = await DemoClient().market("BTC_USDT", "15m")
    anchor = Candle(
        market.candles[-1].time,
        Decimal(40),
        Decimal(100),
        Decimal(1),
        Decimal(50),
        Decimal(0),
    )
    if part == "body":
        before = [replace(anchor, close=Decimal(53))]
        after = [replace(anchor, close=Decimal("53.5"))]
    else:
        candle = Candle(
            anchor.time + 900,
            Decimal(30),
            Decimal("63.25"),
            Decimal(10),
            Decimal(40),
            Decimal(0),
        )
        before, after = [anchor, candle], [anchor, replace(candle, high=Decimal("63.75"))]
    first, second = (replace(market, candles=bars, last=Decimal(80)) for bars in (before, after))

    def panel(data, detail):
        return build_chart(data, None, 74, 17, detail=detail).plain.splitlines()[:12]

    # Fixed outer prices and ticker isolate the candle itself from axes and labels.
    assert panel(first, False) == panel(second, False)
    assert panel(first, True) != panel(second, True)
    assert first.candles == before and second.candles == after


async def test_small_volume_changes_reach_detailed_bars_without_rescaling_the_axis():
    market = await DemoClient().market("ETH_USDT", "15m")
    anchor, bar = market.candles[-2:]
    before, after = (
        replace(market, candles=[replace(anchor, volume=Decimal(100)), replace(bar, volume=value)])
        for value in (Decimal(37), Decimal(39))
    )

    def volume(data, detail):
        return build_chart(data, None, 74, 17, detail=detail).plain.splitlines()[12:]

    assert volume(before, False) == volume(after, False)
    assert volume(before, True) != volume(after, True)
    assert before.candles[-1].volume == 37 and after.candles[-1].volume == 39


@pytest.mark.parametrize("width", [35, 74, 114])
async def test_detailed_wicks_align_with_body_centers_at_subcell_resolution(width):
    market = await DemoClient().market("BTC_USDT", "15m")
    bars = [
        Candle(
            market.candles[-1].time + i * 900,
            Decimal(22),
            Decimal(90),
            Decimal(10),
            Decimal(78),
            Decimal(0),
        )
        for i in range(7)
    ]
    frame = build_chart(replace(market, candles=bars, last=Decimal(80)), None, width, 17)
    dot_rows = {}
    for line in frame.plain.splitlines()[:12]:
        subrows = [set() for _ in range(4)]
        for column, character in enumerate(line):
            if not 0x2800 <= ord(character) <= 0x28FF:
                continue
            dots = ord(character) - 0x2800
            for row, (left, right) in enumerate(((0, 3), (1, 4), (2, 5), (6, 7))):
                if dots & (1 << left):
                    subrows[row].add(column * 2)
                if dots & (1 << right):
                    subrows[row].add(column * 2 + 1)
        for row in subrows:
            dot_rows.setdefault(len(row), []).append(sorted(row))
    body_dots = dot_rows[7 * 3][0]
    wick_dots = dot_rows[7][0]
    assert body_dots[1::3] == wick_dots
    assert all(body_dots[i + 2] - body_dots[i] == 2 for i in range(0, 21, 3))


async def test_candle_detail_toggle_rebuilds_plot_and_survives_symbol_switch_and_resize():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        chart = app.query_one(CandleChart)
        market = app.market
        detailed = chart.render().plain
        assert chart.detail
        assert "FINE" in app.query_one("#chart-pane").border_title
        await pilot.press("v")
        await pilot.pause()
        assert not chart.detail
        assert "SOLID" in app.query_one("#chart-pane").border_title
        assert chart.render().plain != detailed
        assert app.market is market
        await pilot.press("down")
        await app.workers.wait_for_complete()
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert not chart.detail and chart.market.symbol == "BTC_USDT"
        await pilot.press("v")
        await pilot.pause()
        assert chart.detail
        assert any(0x2800 <= ord(c) <= 0x28FF for c in chart.render().plain)


async def test_zoom_recenters_after_loading_another_symbol():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        while list(app.workers):
            await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("plus")
        chart = app.query_one(CandleChart)
        initial_anchor = chart._zoom_anchor
        await pilot.press("down")
        while list(app.workers):
            await app.workers.wait_for_complete()
        await pilot.pause()
        assert chart.market.symbol == "BTC_USDT"
        assert chart.price_zoom == 2
        assert chart._zoom_anchor != initial_anchor
        assert price(chart.market.last) in chart.render().plain
