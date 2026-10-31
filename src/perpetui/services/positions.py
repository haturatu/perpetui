"""Position snapshots, revision guards and streaming independent of the UI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import aclosing, suppress
from datetime import datetime

from perpetui.domain.models import Positions
from perpetui.errors import ExchangeError
from perpetui.exchanges.base import DataSource
from perpetui.i18n import _

logger = logging.getLogger("perpetui")


class PositionsService:
    def __init__(self, client: DataSource) -> None:
        self.client = client
        self.snapshot = Positions([])
        self.error = ""
        self.updated = ""
        self.connection = "connecting" if client.position_streaming else "REST"
        self.revision = 0
        self.busy = False
        self.closed = False
        self.stream_task: asyncio.Task[None] | None = None
        self.on_change: Callable[[], None] = lambda: None

    def accept(self, snapshot: Positions) -> None:
        if self.closed:
            return
        self.revision += 1
        self.snapshot = snapshot
        self.error = ""
        self.updated = datetime.now().strftime("%H:%M:%S")
        self.on_change()

    async def stop_stream(self) -> None:
        if self.stream_task is not None:
            self.stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.stream_task
            self.stream_task = None

    async def load(self, *, restart_stream: bool = False) -> None:
        if self.closed or not self.client.authenticated:
            return
        self.busy = True
        try:
            if restart_stream and self.client.position_streaming:
                await self.stop_stream()
                self.connection = "connecting"
            revision = self.revision
            snapshot = await self.client.positions()
            if self.closed or revision != self.revision:
                return
            self.accept(snapshot)
            if self.client.position_streaming and (
                self.stream_task is None or self.stream_task.done()
            ):
                self.stream_task = asyncio.create_task(self._consume())
        except ExchangeError as exc:
            if self.closed or revision != self.revision:
                return
            self.error = _("Positions unavailable: {error}", error=exc)
            logger.warning("%s", self.error)
            self.on_change()
        finally:
            self.busy = False

    async def _consume(self) -> None:
        try:
            async with aclosing(self.client.stream_positions()) as events:
                async for event in events:
                    if self.closed:
                        return
                    self.connection = event.state
                    if event.positions is not None:
                        self.accept(event.positions)
                    else:
                        self.on_change()
        except ExchangeError as exc:
            logger.warning("Positions stream unavailable (%s); using REST", type(exc).__name__)
        finally:
            if not self.closed:
                self.connection = "reconnecting"
                self.on_change()

    async def close(self) -> None:
        self.closed = True
        await self.stop_stream()
