from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from rich.text import Text
from textual.message import Message
from textual.widget import Widget

from perpetui.domain.history import CandleStore
from perpetui.domain.models import Candle, Market
from perpetui.i18n import _, current_language
from perpetui.ui.formatting import price
from perpetui.ui.widgets.chart_plot import MUTED, build_chart, candle_window


class CandleChart(Widget):
    market: Market | None = None
    entry: Decimal | None = None
    message = "Loading market data…"
    bars_visible = 0
    price_zoom = 1
    detail = True
    cursor_time: int | None = None
    time_zoom = 1
    history_busy = False
    history_exhausted = False
    _zoom_anchor: Decimal | None = None
    _zoom_market_key: tuple[str, str] | None = None
    _cache_key: tuple | None = None
    _cached_plot: Text | None = None

    class CursorMoved(Message):
        def __init__(self, chart: CandleChart, candle: Candle | None) -> None:
            super().__init__()
            self.chart = chart
            self.candle = candle

    class HistoryRequested(Message):
        def __init__(self, chart: CandleChart) -> None:
            super().__init__()
            self.chart = chart

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._store = CandleStore()
        self._market_key: tuple[str, str] | None = None

    def candles(self) -> list[Candle]:
        return self._store.all()

    def reset_market(self) -> None:
        self.market, self.entry = None, None
        self._market_key = None
        self._store.reset([])
        self.cursor_time, self.time_zoom = None, 1
        self.history_exhausted = False
        self._zoom_anchor, self._zoom_market_key = None, None
        self._cache_key, self._cached_plot = None, None
        self.message = _("Loading market data…")
        self.refresh()

    def set_market(self, market: Market, entry: Decimal | None = None) -> None:
        key = (market.symbol, market.interval)
        if key != self._market_key:
            self._market_key = key
            self.cursor_time, self.time_zoom = None, 1
            self.history_exhausted = False
            self._store.reset(market.candles)
        else:
            self._store.merge_live(market.candles)
        if (
            self.price_zoom > 1
            and market.candles
            and (self._zoom_anchor is None or self._zoom_market_key != key)
        ):
            self._zoom_anchor = market.candles[-1].close
            self._zoom_market_key = key
        self.market, self.entry = market, entry
        self.refresh()

    def prepend_history(self, candles: list[Candle]) -> bool:
        added = self._store.prepend(candles)
        if added:
            self._cache_key, self._cached_plot = None, None
            self.refresh()
        return added

    def mark_history_exhausted(self) -> None:
        self.history_exhausted = True

    def earliest_time(self) -> int | None:
        return self._store.earliest_time()

    def selected_candle(self) -> Candle | None:
        candles = self._store.all()
        if not candles:
            return None
        if self.cursor_time is None:
            return candles[-1]
        return self._store.nearest(self.cursor_time)

    def cursor_offset(self) -> int:
        candles = self._store.all()
        if self.cursor_time is None or not candles:
            return 0
        index = self._store.index_at_or_before(self.cursor_time)
        return 0 if index is None else index - (len(candles) - 1)

    def mode_label(self) -> str:
        offset = self.cursor_offset()
        return _("LIVE") if offset == 0 else _("HISTORY") + f" {offset:+d}"

    def _move(self, timestamp: int | None, *, notify: bool = True) -> None:
        if timestamp == self.cursor_time:
            return
        self.cursor_time = timestamp
        self._cache_key, self._cached_plot = None, None
        self.refresh()
        if notify:
            self.post_message(self.CursorMoved(self, self.selected_candle()))
        if self.needs_history():
            self.post_message(self.HistoryRequested(self))

    def step_cursor(self, delta: int) -> None:
        candles = self._store.all()
        if not candles:
            return
        index = (
            len(candles) - 1
            if self.cursor_time is None
            else self._store.index_at_or_before(self.cursor_time)
        )
        if index is None:
            return
        target = max(0, min(len(candles) - 1, index + delta))
        self._move(None if target == len(candles) - 1 else candles[target].time)

    def first_candle(self) -> None:
        candles = self._store.all()
        if candles:
            self._move(candles[0].time)

    def live_candle(self) -> None:
        self._move(None)

    def page_cursor(self, direction: int) -> None:
        self.step_cursor(direction * max(1, self.bars_visible - 2))

    def select_time(self, timestamp: int | None, *, notify: bool = True) -> None:
        if timestamp is None:
            self._move(None, notify=notify)
            return
        candles = self._store.all()
        index = self._store.index_at_or_before(timestamp)
        if not candles or index is None:
            return
        self._move(None if index == len(candles) - 1 else candles[index].time, notify=notify)

    def zoom_time(self, direction: int) -> None:
        steps = (1, 2, 3, 4, 6, 8)
        if direction == 0 or self.time_zoom not in steps:
            self.time_zoom = 1
        else:
            index = steps.index(self.time_zoom) + (1 if direction > 0 else -1)
            self.time_zoom = steps[max(0, min(len(steps) - 1, index))]
        self._cache_key, self._cached_plot = None, None
        self.refresh()

    def needs_history(self) -> bool:
        if self.history_exhausted or self.history_busy or self.cursor_time is None:
            return False
        index = self._store.index_at_or_before(self.cursor_time)
        return index is not None and index <= max(5, self.bars_visible // 3)

    def zoom_price(self, direction: int) -> None:
        candle = self.selected_candle()
        if direction > 0 and self.price_zoom == 1 and candle:
            self._zoom_anchor = candle.close
            self._zoom_market_key = self._market_key
        self.price_zoom = (
            1
            if direction == 0
            else min(64, self.price_zoom * 2)
            if direction > 0
            else max(1, self.price_zoom // 2)
        )
        if self.price_zoom == 1:
            self._zoom_anchor = None
            self._zoom_market_key = None
        self.refresh()

    def toggle_detail(self) -> None:
        self.detail = not self.detail
        self.refresh()

    def render(self) -> Text:
        width, height = self.content_size
        candles = self._store.all()
        if not self.market or not candles:
            self.bars_visible = 0
            return Text("\n  " + _(self.message), style=MUTED)
        if width < 30 or height < 6:
            self.bars_visible = 0
            return Text(_("Enlarge terminal to see the chart"), style=MUTED)
        key = (
            self.market,
            self.entry,
            width,
            height,
            self.price_zoom,
            self._zoom_anchor,
            self.detail,
            current_language(),
            self.cursor_time,
            self.time_zoom,
            self._store.version,
        )
        if self._cache_key != key or self._cached_plot is None:
            plot_market = replace(self.market, candles=candles)
            self._cached_plot = build_chart(
                plot_market,
                self.entry,
                width,
                height,
                self.price_zoom,
                self._zoom_anchor,
                self.detail,
                self.cursor_time,
                self.time_zoom,
            )
            self._cache_key = key
        label_width = max(12, len(price(max(bar.high for bar in candles))) + 2)
        count = max(1, (width - label_width - 1) // 2) * max(1, self.time_zoom)
        window = candle_window(candles, count, self.cursor_time)[0]
        self.bars_visible = len(window)
        return self._cached_plot
