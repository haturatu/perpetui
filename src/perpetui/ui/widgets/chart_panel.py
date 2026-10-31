"""Chart presentation; all network activity belongs to ChartSession."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message

from perpetui.domain.models import Market
from perpetui.i18n import _
from perpetui.services.chart_session import ChartSession
from perpetui.ui.widgets.chart import CandleChart


class ChartFrame(Vertical):
    can_focus = True
    BINDINGS = [
        Binding("left", "move_candle(-1)", "Previous candle", show=False),
        Binding("right", "move_candle(1)", "Next candle", show=False),
        Binding("shift+left", "move_candle(-5)", "Back 5 candles", show=False),
        Binding("shift+right", "move_candle(5)", "Forward 5 candles", show=False),
        Binding("home", "first_candle", "First candle", show=False),
        Binding("end", "live_candle", "Return to live", show=False),
        Binding("pageup", "page_cursor(-1)", "Previous page", show=False),
        Binding("pagedown", "page_cursor(1)", "Next page", show=False),
    ]

    class Selected(Message):
        def __init__(self, frame: ChartFrame) -> None:
            super().__init__()
            self.frame = frame

    def on_focus(self) -> None:
        self.post_message(self.Selected(self))

    def on_click(self) -> None:
        self.focus()

    def chart(self) -> CandleChart:
        return self.query_one(CandleChart)

    def action_move_candle(self, delta: int) -> None:
        self.chart().step_cursor(delta)

    def action_first_candle(self) -> None:
        self.chart().first_candle()

    def action_live_candle(self) -> None:
        self.chart().live_candle()

    def action_page_cursor(self, direction: int) -> None:
        self.chart().page_cursor(direction)


class ChartPanel(ChartFrame):
    class Updated(Message):
        def __init__(self, panel: ChartPanel) -> None:
            super().__init__()
            self.panel = panel

    def __init__(
        self,
        session: ChartSession,
        *,
        entry: Decimal | None = None,
        detail: bool = True,
        zoom: int = 1,
        autoload: bool = True,
    ) -> None:
        index = session.state.chart_id
        super().__init__(
            id="chart-pane" if index == 1 else f"chart-pane-{index}",
            classes="chart-frame active" if index == 1 else "chart-frame",
        )
        self.session, self.index = session, index
        self.entry, self.detail, self.zoom, self.autoload = entry, detail, zoom, autoload
        self.manual_symbol = False
        self.numbered = index != 1

    @property
    def symbol(self) -> str:
        return self.session.state.symbol

    @property
    def interval(self) -> str:
        return self.session.state.interval

    @property
    def market(self) -> Market | None:
        return self.session.state.market

    @property
    def error(self) -> str:
        return self.session.state.error

    @property
    def updated(self) -> str:
        return self.session.state.updated

    @property
    def busy(self) -> bool:
        return self.session.busy

    @property
    def state(self) -> str:
        return self.session.state.connection

    def compose(self) -> ComposeResult:
        chart = CandleChart(id="chart" if self.index == 1 else f"chart-{self.index}")
        chart.detail, chart.price_zoom = self.detail, self.zoom
        yield chart

    def on_mount(self) -> None:
        self.session.on_change = self._session_changed
        self.update_title()
        if self.autoload:
            self.load_market()

    def _session_changed(self) -> None:
        if not self.is_mounted:
            return
        chart = self.query_one(CandleChart)
        if self.market is not None:
            chart.set_market(self.market, self.entry)
        else:
            chart.reset_market()
            chart.message = (
                _("Chart unavailable · press r to retry")
                if self.error
                else _(
                    "Loading {symbol} · {interval}…",
                    symbol=self.symbol.replace("_", " / "),
                    interval=self.interval,
                )
            )
            chart.refresh()
        self.update_title()
        self.post_message(self.Updated(self))

    def update_title(self) -> None:
        chart = self.query_one(CandleChart)
        zoom = chart.price_zoom
        zoom_label = f" · {_('PRICE')} ×{zoom}" if zoom > 1 else ""
        detail = _("FINE") if chart.detail else _("SOLID")
        prefix = f"{self.index} · " if self.numbered else ""
        self.border_title = (
            f" {prefix}{self.symbol.replace('_', ' / ')} · {self.interval} · "
            f"{_('PERPETUAL')} · {detail}{zoom_label} · {chart.mode_label()} "
        )

    def change(self, *, symbol: str | None = None, interval: str | None = None) -> None:
        self.session.configure(symbol=symbol, interval=interval)
        self.autoload = True
        self.load_market()

    def set_entry(self, entry: Decimal | None) -> None:
        self.entry = entry
        chart = self.query_one(CandleChart)
        if chart.entry != entry:
            chart.entry = entry
            chart.refresh()

    async def stop(self) -> None:
        self.session.on_change = lambda: None
        workers = self.workers.cancel_group(self, "market")
        await asyncio.gather(*(worker.wait() for worker in workers), return_exceptions=True)
        await self.session.stop()

    async def on_unmount(self) -> None:
        await self.stop()

    @work(exclusive=True, group="market")
    async def load_market(self, restart_stream: bool = True) -> None:
        await self.session.load(restart_stream=restart_stream)

    def refresh_data(self) -> None:
        if self.autoload and not self.session.stopped and not self.busy and self.state != "live":
            self.load_market(restart_stream=False)


class ExtraChart(ChartPanel):
    """A secondary pane uses exactly the same lifecycle as the first pane."""
