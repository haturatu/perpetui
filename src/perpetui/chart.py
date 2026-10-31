"""Plotext 6 candles and volume rendered as Rich text inside Textual."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import plotext as plt
from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual.message import Message
from textual.widget import Widget

from .history import CandleStore
from .i18n import _, current_language
from .models import INTERVALS, Candle, Market, price

UP = "#2dd4bf"
DOWN = "#fb7185"
MUTED = "#788ba9"
GRID = "#233149"
ACCENT = "#7aa2ff"
ENTRY = "#e7bd68"
BACKGROUND = (8, 14, 24)


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def _pixel(color: str, *, bold: bool = False) -> plt.pixel:
    """Plotext expects RGB tuples rather than CSS hex color strings."""
    return plt.pixel(_rgb(color), BACKGROUND, "bold" if bold else None)


def _solid_blocks(text: Text) -> Text:
    """Fill complete cells without relying on the font's full-block glyph height."""
    result = Text(no_wrap=True)
    for segment in text.render(Console()):
        style = segment.style
        if "█" not in segment.text or style is None or style.color is None:
            result.append(segment.text, style=style)
            continue
        parts = segment.text.split("█")
        for part in parts[:-1]:
            result.append(part, style=style)
            result.append(" ", style=style + Style(bgcolor=style.color))
        result.append(parts[-1], style=style)
    return result


def _draw_candles(
    panel,
    candles: list[Candle],
    limits: tuple[float, float],
    columns: int,
    price_limits: tuple[Decimal, Decimal],
    detail: bool,
) -> None:
    """Use a common sub-cell grid for detailed candle bodies and wicks."""
    lower, upper = limits
    unit = (upper - lower) / columns
    price_low, price_high = price_limits
    for candle in candles:
        if candle.high < price_low or candle.low > price_high:
            continue
        column = min(columns - 1, max(0, math.floor((candle.time - lower) / unit)))
        # Braille bodies span three dots, with the wick on the middle dot.
        # An odd dot width avoids shifting the wick to one side of a two-dot body.
        center = lower + (column + (0.25 if detail else 0.5)) * unit
        body_left = max(lower, lower + (column + (-0.4 if detail else 0.1)) * unit)
        pixel = _pixel(UP if candle.close >= candle.open else DOWN, bold=detail)
        body_marker = plt.marker("braille" if detail else "hd", pixel)
        wick_marker = plt.marker("braille", pixel) if detail else plt.line(1, pixel)
        panel.draw(
            panel.segment(
                (center, center),
                (float(max(candle.low, price_low)), float(min(candle.high, price_high))),
                marker=wick_marker,
                yside="right",
            ).density("full")
        )
        body_low, body_high = sorted((candle.open, candle.close))
        if body_high < price_low or body_low > price_high:
            continue
        panel.draw(
            panel.rectangle(
                (body_left, lower + (column + 0.9) * unit),
                (float(max(body_low, price_low)), float(min(body_high, price_high))),
                marker=body_marker,
                yside="right",
            ).density("full")
        )


def candle_window(
    candles: list[Candle], count: int, cursor_time: int | None = None
) -> tuple[list[Candle], int | None]:
    """Return the visible slice and the cursor index inside that slice."""
    if not candles:
        return [], None
    count = max(1, min(len(candles), count))
    if cursor_time is None:
        start = len(candles) - count
    else:
        index = bisect_right([bar.time for bar in candles], cursor_time) - 1
        index = max(0, min(len(candles) - 1, index))
        start = max(0, min(index - count // 2, len(candles) - count))
    window = candles[start : start + count]
    if cursor_time is None:
        return window, None
    index = next(
        (i for i, bar in enumerate(window) if bar.time >= cursor_time),
        len(window) - 1,
    )
    return window, index


def build_chart(
    market: Market,
    entry: Decimal | None,
    width: int,
    height: int,
    zoom: int = 1,
    anchor: Decimal | None = None,
    detail: bool = True,
    cursor_time: int | None = None,
    time_zoom: int = 1,
) -> Text:
    """Build synchronously: Plotext's public figure is shared across its session."""
    label_width = max(12, len(price(max(bar.high for bar in market.candles))) + 2)
    count = max(1, (width - label_width - 1) // 2) * max(1, time_zoom)
    candles = candle_window(market.candles, count, cursor_time)[0]
    seconds = INTERVALS[market.interval][1]
    times = [bar.time for bar in candles]
    low, high = min(bar.low for bar in candles), max(bar.high for bar in candles)
    padding = (high - low) * Decimal(".07") or abs(high) * Decimal(".001") or Decimal(".0001")
    low, high = low - padding, high + padding
    if zoom > 1:
        half_range = (high - low) / (2 * zoom)
        center = anchor if anchor is not None else candles[-1].close
        low, high = center - half_range, center + half_range
    volume_height = 5 if height >= 12 else 3
    price_height = height - volume_height

    # Textual determines the dimensions, independently of the process's tty size.
    plt.terminal.limit(False, False)
    figure = plt.figure
    figure.clear()
    figure.plot_size(width, height)
    plt.add_theme("perpetui", canvas=BACKGROUND, text=_pixel(MUTED))
    figure.theme("perpetui")
    figure.canvas(BACKGROUND)
    figure.axes(False)
    figure.ruler("both", "both").frequency(0).pixel(_pixel(MUTED))
    figure.subplots(2, 1)
    prices, volume = figure.subplot(1, 1), figure.subplot(2, 1)
    prices.plot_size(width, price_height)
    volume.plot_size(width, volume_height)
    x_limits = (times[0] - seconds / 2, times[-1] + seconds / 2)
    for panel in (prices, volume):
        panel.canvas(BACKGROUND)
        panel.axes(False)
        panel.ruler("both", "both").frequency(0).pixel(_pixel(MUTED))
        panel.ruler("x").lim(*x_limits).alignment(lim="edge")
        panel.axes(True, axis="y", side="right", pixel=_pixel(GRID))

    prices.ruler("y", "right").lim(float(low), float(high))
    _draw_candles(prices, candles, x_limits, width - label_width - 1, (low, high), detail)
    if cursor_time is not None and any(bar.time == cursor_time for bar in candles):
        prices.line(
            float(cursor_time),
            orientation=1,
            relative=False,
            style="dotted",
            pixel=_pixel(ACCENT),
            yside="right",
        )

    # Replace nearby tick labels with current price / entry, keeping overlays readable.
    span = high - low
    levels: dict[int, tuple[Decimal, str, str]] = {}

    def tick(value: Decimal, label: str, color: str) -> None:
        row = round((high - value) / span * max(1, price_height - 1))
        levels[row] = (value, label.rjust(label_width), color)

    for i in range(min(5, price_height)):
        value = low + span * i / max(1, min(5, price_height) - 1)
        tick(value, price(value), MUTED)
        prices.line(float(value), style="dotted", pixel=_pixel(GRID), yside="right")
    if low <= market.last <= high:
        tick(market.last, price(market.last), UP if market.change >= 0 else DOWN)
        prices.line(float(market.last), style="dotted", pixel=_pixel(ACCENT), yside="right")
    if entry is not None and low <= entry <= high:
        tick(entry, "E " + price(entry), ENTRY)
        prices.line(float(entry), style="dotted", pixel=_pixel(ENTRY), yside="right")
    ticks = sorted(levels.values())
    prices.ruler("y", "right").ticks(
        [float(value) for value, _, _ in ticks],
        [plt.colorize(label, _pixel(color)) for _, label, color in ticks],
    )

    maximum = max(bar.volume for bar in candles) or Decimal(1)
    volume.ruler("y", "right").lim(0, float(maximum))
    volume.draw(
        volume.bar(
            times,
            [float(bar.volume) for bar in candles],
            width=0.65,
            marker=[
                plt.marker(
                    "braille" if detail else "hd",
                    _pixel(UP if bar.close >= bar.open else DOWN, bold=detail),
                )
                for bar in candles
            ],
            lines=False,
            yside="right",
        )
    )
    volume_label = _("VOL")
    volume_label = " " * max(0, label_width - cell_len(volume_label)) + volume_label
    volume.ruler("y", "right").ticks([float(maximum)], [volume_label])
    volume.axes(True, axis="x", side="lower", pixel=_pixel(GRID))
    indices = sorted({0, len(candles) - 1})
    if width - label_width - 1 >= 45:
        indices = sorted({*indices, len(candles) // 2})
    date_format = "%m/%d" if market.interval in {"1d", "1w"} else "%H:%M"
    labels = [datetime.fromtimestamp(times[i]).astimezone().strftime(date_format) for i in indices]
    labels[-1] += " " + _("LOCAL")
    volume.ruler("x").ticks([times[i] for i in indices], labels)

    output = _solid_blocks(Text.from_ansi(figure.build().string()))
    # A trailing newline would make Textual allocate an extra, empty chart row.
    output.rstrip()
    output.no_wrap = True
    return output


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
