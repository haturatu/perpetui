from __future__ import annotations

import math
from bisect import bisect_right
from datetime import datetime
from decimal import Decimal

import plotext as plt
from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text

from perpetui.domain.intervals import INTERVAL_SECONDS
from perpetui.domain.models import Candle, Market
from perpetui.i18n import _
from perpetui.ui.formatting import price
from perpetui.ui.theme import ACCENT, BACKGROUND, DOWN, ENTRY, GRID, MUTED, UP


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
    seconds = INTERVAL_SECONDS[market.interval]
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
