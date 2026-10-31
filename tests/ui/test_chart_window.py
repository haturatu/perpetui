from decimal import Decimal

from perpetui.domain.models import Candle
from perpetui.ui.widgets.chart_plot import candle_window


def bar(timestamp, closing="100"):
    return Candle(
        timestamp,
        Decimal("99"),
        Decimal("101"),
        Decimal("98"),
        Decimal(closing),
        Decimal("10"),
    )


def test_window_centres_the_cursor_and_sticks_to_the_end_when_live():
    candles = [bar(index) for index in range(1, 21)]
    window, index = candle_window(candles, 7)
    assert [item.time for item in window] == list(range(14, 21))
    assert index is None
    window, index = candle_window(candles, 7, cursor_time=10)
    assert [item.time for item in window] == list(range(7, 14))
    assert window[index].time == 10
    window, index = candle_window(candles, 7, cursor_time=19)
    assert [item.time for item in window] == list(range(14, 21))
    assert window[index].time == 19
