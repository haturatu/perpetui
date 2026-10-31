"""Independently loaded and streamed chart panes for the multi-chart grid."""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing, suppress
from datetime import datetime
from decimal import Decimal

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message

from .chart import CandleChart
from .client import DataSource
from .errors import ExchangeError
from .i18n import _
from .models import Market

logger = logging.getLogger("perpetui")


class ChartFrame(Vertical):
    can_focus = True

    class Selected(Message):
        def __init__(self, frame: ChartFrame) -> None:
            super().__init__()
            self.frame = frame

    def on_focus(self) -> None:
        self.post_message(self.Selected(self))

    def on_click(self) -> None:
        self.focus()


class ExtraChart(ChartFrame):
    class Updated(Message):
        def __init__(self, panel: ExtraChart) -> None:
            super().__init__()
            self.panel = panel

    def __init__(
        self,
        client: DataSource,
        index: int,
        symbol: str,
        interval: str,
        *,
        entry: Decimal | None = None,
        detail: bool = True,
        zoom: int = 1,
        autoload: bool = True,
    ) -> None:
        super().__init__(id=f"chart-pane-{index}", classes="chart-frame")
        self.client, self.index = client, index
        self.symbol, self.interval, self.entry = symbol, interval, entry
        self.detail, self.autoload = detail, autoload
        self.manual_symbol = False
        self.zoom = zoom
        self.market: Market | None = None
        self.error = ""
        self.updated = ""
        self.busy = False
        self.state = "connecting" if client.streaming else "REST"
        self._stream_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._stopped = False

    def compose(self) -> ComposeResult:
        chart = CandleChart(id=f"chart-{self.index}")
        chart.detail = self.detail
        chart.price_zoom = self.zoom
        yield chart

    def on_mount(self) -> None:
        self.update_title()
        if self.autoload:
            self.load_market()

    def update_title(self) -> None:
        zoom = self.query_one(CandleChart).price_zoom
        zoom_label = f" · {_('PRICE')} ×{zoom}" if zoom > 1 else ""
        detail = _("FINE") if self.query_one(CandleChart).detail else _("SOLID")
        self.border_title = (
            f" {self.index} · {self.symbol.replace('_', ' / ')} · "
            f"{self.interval} · {detail}{zoom_label} "
        )

    def change(self, *, symbol: str | None = None, interval: str | None = None) -> None:
        if symbol is not None:
            self.symbol = symbol
        if interval is not None:
            self.interval = interval
        self._generation += 1
        self.autoload = True
        self.load_market()

    def set_entry(self, entry: Decimal | None) -> None:
        self.entry = entry
        chart = self.query_one(CandleChart)
        if chart.entry != entry:
            chart.entry = entry
            chart.refresh()

    async def _stop_stream(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
            self._stream_task = None

    async def stop(self) -> None:
        self._stopped = True
        self._generation += 1
        workers = self.workers.cancel_group(self, "market")
        await asyncio.gather(*(worker.wait() for worker in workers), return_exceptions=True)
        await self._stop_stream()

    async def on_unmount(self) -> None:
        await self.stop()

    @work(exclusive=True, group="market")
    async def load_market(self, restart_stream: bool = True) -> None:
        generation = self._generation
        symbol, interval = self.symbol, self.interval
        self.busy = True
        if restart_stream:
            await self._stop_stream()
            self.state = "connecting" if self.client.streaming else "REST"
        if self._stopped or generation != self._generation or not self.query(CandleChart):
            return
        chart = self.query_one(CandleChart)
        if self.market is None or (self.market.symbol, self.market.interval) != (symbol, interval):
            chart.reset_market()
            self.market, self.error, self.updated = None, "", ""
            self.post_message(self.Updated(self))
        self.update_title()
        try:
            market = await self.client.market(symbol, interval)
            if generation != self._generation or self._stopped:
                return
            if not restart_stream and self.state == "live":
                return
            self.symbol, self.market, self.error = market.symbol, market, ""
            self.updated = datetime.now().strftime("%H:%M:%S")
            chart.set_market(market, self.entry)
            self.update_title()
            self.post_message(self.Updated(self))
            if self.client.streaming and (self._stream_task is None or self._stream_task.done()):
                self._stream_task = asyncio.create_task(self._consume(market, generation))
        except ExchangeError as exc:
            if (
                generation != self._generation
                or self._stopped
                or (not restart_stream and self.state == "live")
            ):
                return
            self.error = _("Chart unavailable: {error}", error=exc)
            logger.warning("%s", self.error)
            if chart.market is None:
                chart.message = _("Chart unavailable · press r to retry")
                chart.refresh()
            self.post_message(self.Updated(self))
        finally:
            if generation == self._generation:
                self.busy = False

    async def _consume(self, seed: Market, generation: int) -> None:
        try:
            async with aclosing(self.client.stream_market(seed)) as stream:
                async for event in stream:
                    if generation != self._generation or self._stopped:
                        return
                    self.state = event.state
                    if event.market is not None:
                        self.market, self.error = event.market, ""
                        self.updated = datetime.now().strftime("%H:%M:%S")
                        self.query_one(CandleChart).set_market(event.market, self.entry)
                        self.post_message(self.Updated(self))
        except ExchangeError as exc:
            logger.warning("Chart stream unavailable (%s)", type(exc).__name__)
            if generation == self._generation:
                self.state = "reconnecting"

    def refresh_data(self) -> None:
        if self.autoload and not self._stopped and not self.busy and self.state != "live":
            self.load_market(restart_stream=False)
