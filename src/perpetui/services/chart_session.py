"""The same market-data lifecycle for every chart, without Textual dependencies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass
from datetime import datetime

from perpetui.domain.models import Market
from perpetui.errors import ExchangeError
from perpetui.i18n import _
from perpetui.services.market_feed import MarketFeedHub

logger = logging.getLogger("perpetui")


@dataclass
class ChartState:
    chart_id: int
    symbol: str
    interval: str
    market: Market | None = None
    error: str = ""
    updated: str = ""
    connection: str = "connecting"


class ChartSession:
    def __init__(self, hub: MarketFeedHub, chart_id: int, symbol: str, interval: str) -> None:
        self.hub = hub
        self.state = ChartState(
            chart_id, symbol, interval, connection="connecting" if hub.client.streaming else "REST"
        )
        self.on_change: Callable[[], None] = lambda: None
        self.busy = False
        self.generation = 0
        self.stopped = False
        self.stream_task: asyncio.Task[None] | None = None

    def configure(self, *, symbol: str | None = None, interval: str | None = None) -> None:
        key = (self.state.symbol, self.state.interval)
        if symbol is not None:
            self.state.symbol = symbol
        if interval is not None:
            self.state.interval = interval
        if key != (self.state.symbol, self.state.interval):
            self.generation += 1
            self.state.market = None
            self.state.error = self.state.updated = ""
            self.on_change()

    async def stop_stream(self) -> None:
        if self.stream_task is not None:
            self.stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.stream_task
            self.stream_task = None

    async def stop(self) -> None:
        self.stopped = True
        self.generation += 1
        self.busy = False
        await self.stop_stream()

    def _accept(self, market: Market) -> None:
        self.state.symbol, self.state.market, self.state.error = market.symbol, market, ""
        self.state.updated = datetime.now().strftime("%H:%M:%S")
        self.on_change()

    async def load(self, *, restart_stream: bool = True) -> None:
        generation = self.generation
        symbol, interval = self.state.symbol, self.state.interval
        self.busy = True
        try:
            if restart_stream:
                await self.stop_stream()
                self.state.connection = "connecting" if self.hub.client.streaming else "REST"
            if self.stopped or generation != self.generation:
                return
            market = await self.hub.snapshot(symbol, interval, refresh=not restart_stream)
            if self.stopped or generation != self.generation:
                return
            if not restart_stream and self.state.connection == "live":
                return
            self._accept(market)
            if self.hub.client.streaming and (self.stream_task is None or self.stream_task.done()):
                self.stream_task = asyncio.create_task(self._consume(market, generation))
        except ExchangeError as exc:
            if self.stopped or generation != self.generation:
                return
            if not restart_stream and self.state.connection == "live":
                return
            self.state.error = _("Chart unavailable: {error}", error=exc)
            logger.warning("%s", self.state.error)
            self.on_change()
        finally:
            if generation == self.generation:
                self.busy = False

    async def _consume(self, seed: Market, generation: int) -> None:
        try:
            async with aclosing(self.hub.subscribe(seed)) as events:
                async for event in events:
                    if generation != self.generation or self.stopped:
                        return
                    self.state.connection = event.state
                    if event.market is not None:
                        self._accept(event.market)
                    else:
                        self.on_change()
        except ExchangeError as exc:
            logger.warning("Chart stream unavailable (%s); using REST", type(exc).__name__)
        finally:
            if generation == self.generation and not self.stopped:
                self.state.connection = "reconnecting"
                self.on_change()
