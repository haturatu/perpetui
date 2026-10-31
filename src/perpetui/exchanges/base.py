from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from perpetui.domain.models import Candle, Market, MarketEvent, Positions, PositionsEvent


class DataSource(Protocol):
    exchange: str
    default_symbol: str
    quantity_label: str
    public_hint: str
    authenticated: bool
    label: str
    streaming: bool
    position_streaming: bool
    intervals: tuple[str, ...]

    def normalize_symbol(self, value: str) -> str: ...

    async def positions(self) -> Positions: ...
    async def symbols(self) -> list[str]: ...
    async def market(self, symbol: str, interval: str) -> Market: ...
    async def history(
        self, symbol: str, interval: str, before: int, limit: int = 500
    ) -> list[Candle]: ...
    def stream_market(self, seed: Market) -> AsyncIterator[MarketEvent]: ...
    def stream_positions(self) -> AsyncIterator[PositionsEvent]: ...
    async def close(self) -> None: ...
