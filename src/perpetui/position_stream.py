"""Authenticated position changes and public fair prices on one persistent socket."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .credentials import Credentials
from .errors import MEXCError
from .i18n import _
from .models import Position, Positions, PositionsEvent, number
from .stream import DEFAULT_WS_URL, decode_message

logger = logging.getLogger("perpetui")


def parse_position(row: dict[str, Any], previous: Position | None = None) -> Position:
    """Parse the fields shared by REST snapshots and private WebSocket updates."""
    symbol = row.get("symbol", previous.symbol if previous else None)
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("Invalid position symbol")
    side = int(row.get("positionType", (1 if previous.side == "LONG" else 2) if previous else 0))
    if side not in (1, 2):
        raise ValueError("Invalid position side")
    opening = row.get("holdAvgPriceFullyScale") or row.get("holdAvgPrice")
    opening = opening or row.get("openAvgPriceFullyScale") or row.get("openAvgPrice")
    margin_mode = previous.margin_mode if previous else "Isolated"
    if "openType" in row:
        margin_mode = "Isolated" if int(row["openType"]) == 1 else "Cross"
    return Position(
        id=str(row["positionId"]),
        symbol=symbol,
        side="LONG" if side == 1 else "SHORT",
        contracts=number(row.get("holdVol", previous.contracts if previous else None)),
        entry=number(opening or (previous.entry if previous else None)),
        liquidation=number(row.get("liquidatePrice", previous.liquidation if previous else 0) or 0),
        leverage=int(row.get("leverage", previous.leverage if previous else 1) or 1),
        margin=number(row.get("im", row.get("oim", previous.margin if previous else 0)) or 0),
        margin_mode=margin_mode,
        settlement=previous.settlement if previous else symbol.rsplit("_", 1)[-1],
        mark=previous.mark if previous else None,
        contract_size=previous.contract_size if previous else None,
        updated_at=int(row.get("updateTime", previous.updated_at if previous else 0) or 0),
        version=int(row.get("version", previous.version if previous else 0) or 0),
    )


class PositionBook:
    """Keep all held sides, including tombstones that reject late reopen events."""

    def __init__(self, snapshot: Positions, details: dict[str, dict[str, Any]]) -> None:
        self.details = details
        self.items = {}
        for position in snapshot.items:
            detail = details.get(position.symbol, {})
            self.items[position.id] = replace(
                position,
                contract_size=number(detail["contractSize"])
                if "contractSize" in detail
                else position.contract_size,
                settlement=str(detail.get("settleCoin", position.settlement)),
            )
        self.versions = {p.id: (p.version, p.updated_at) for p in snapshot.items}
        self.marks = {p.symbol: (0, p.mark) for p in snapshot.items if p.mark is not None}

    @property
    def symbols(self) -> set[str]:
        return {p.symbol for p in self.items.values()}

    def snapshot(self) -> Positions:
        missing = list(
            dict.fromkeys(
                p.symbol for p in self.items.values() if p.mark is None or p.contract_size is None
            )
        )
        warnings = (
            (_("Mark / PnL unavailable: {symbols}", symbols=", ".join(missing)),) if missing else ()
        )
        return Positions(list(self.items.values()), warnings)

    def apply(self, message: dict[str, Any]) -> bool:
        channel, data = message.get("channel"), message.get("data")
        if channel not in {"push.personal.position", "push.fair.price"}:
            return False
        if not isinstance(data, dict):
            raise ValueError("Invalid position stream data")
        if channel == "push.fair.price":
            symbol = str(data.get("symbol", message.get("symbol", "")))
            if symbol not in self.symbols:
                return False
            stamp = int(message.get("ts", 0) or 0)
            previous_stamp = self.marks.get(symbol, (0, None))[0]
            if stamp and stamp < previous_stamp:
                return False
            mark = number(data["price"])
            if mark <= 0:
                raise ValueError("Invalid fair price")
            self.marks[symbol] = (stamp or previous_stamp, mark)
            changed = False
            for key, position in self.items.items():
                if position.symbol == symbol and position.mark != mark:
                    self.items[key] = replace(position, mark=mark)
                    changed = True
            return changed

        key = str(data["positionId"])
        version = int(data.get("version", 0) or 0)
        stamp = int(data.get("updateTime") or message.get("ts") or 0)
        old_version, old_stamp = self.versions.get(key, (0, 0))
        if version and old_version:
            if version < old_version or (version == old_version and stamp <= old_stamp):
                return False
        elif stamp and stamp < old_stamp:
            return False
        previous = self.items.get(key)
        state = int(data.get("state", 1))
        volume = number(data.get("holdVol", previous.contracts if previous else 0))
        if state not in (1, 2, 3) or volume < 0:
            raise ValueError("Invalid position state or quantity")
        if state == 3 or volume == 0:
            self.items.pop(key, None)
            self.versions[key] = (version or old_version, stamp or old_stamp)
            if previous and previous.symbol not in self.symbols:
                self.marks.pop(previous.symbol, None)
            return previous is not None
        position = parse_position(data, previous)
        if previous and position.symbol != previous.symbol:
            raise ValueError("Position symbol changed")
        detail = self.details.get(position.symbol, {})
        position = replace(
            position,
            mark=self.marks.get(position.symbol, (0, position.mark))[1],
            contract_size=number(detail["contractSize"])
            if "contractSize" in detail
            else position.contract_size,
            settlement=str(detail.get("settleCoin", position.settlement)),
            updated_at=stamp,
            version=version or old_version,
        )
        if position.entry <= 0:
            raise ValueError("Invalid entry price")
        self.items[key] = position
        self.versions[key] = (version or old_version, stamp or old_stamp)
        return position != previous


class PositionsStream:
    def __init__(
        self,
        credentials: Credentials,
        load_positions: Callable[[], Awaitable[Positions]],
        load_details: Callable[[], Awaitable[dict[str, dict[str, Any]]]],
        *,
        url: str = DEFAULT_WS_URL,
        timeout: float = 10,
        heartbeat: float = 15,
        stale_after: float = 45,
        retry_delay: float = 1,
    ) -> None:
        self.credentials = credentials
        self.load_positions = load_positions
        self.load_details = load_details
        self.url = url
        self.timeout = timeout
        self.heartbeat = heartbeat
        self.stale_after = stale_after
        self.retry_delay = retry_delay

    async def _heartbeat(self, websocket: ClientConnection) -> None:
        while True:
            await websocket.send('{"method":"ping"}')
            await asyncio.sleep(self.heartbeat)

    @staticmethod
    def _check_response(message: dict[str, Any]) -> None:
        channel = message.get("channel", "")
        if not isinstance(channel, str):
            raise ValueError("Invalid positions WebSocket channel")
        if (
            channel == "rs.error"
            or message.get("code") not in (None, 0, "0")
            or (channel.startswith("rs.") and message.get("data") != "success")
        ):
            raise MEXCError("Positions WebSocket request rejected")

    async def _expect(
        self, websocket: ClientConnection, channel: str, pending: deque[dict[str, Any]]
    ) -> None:
        async with asyncio.timeout(self.timeout):
            while True:
                message = decode_message(await websocket.recv())
                self._check_response(message)
                if message.get("channel") == channel:
                    return
                pending.append(message)

    async def _subscribe_prices(
        self,
        websocket: ClientConnection,
        symbols: set[str],
        previous: set[str],
        pending: deque[dict[str, Any]],
    ) -> None:
        for method, changed in (
            ("unsub.fair.price", previous - symbols),
            ("sub.fair.price", symbols - previous),
        ):
            for symbol in sorted(changed):
                await websocket.send(
                    json.dumps(
                        {
                            "method": method,
                            "param": {"symbol": symbol},
                            "gzip": False,
                        }
                    )
                )
                await self._expect(websocket, "rs." + method, pending)

    async def watch(self) -> AsyncIterator[PositionsEvent]:
        retry = False
        delay = self.retry_delay
        while True:
            yield PositionsEvent("reconnecting" if retry else "connecting")
            try:
                async with connect(
                    self.url,
                    open_timeout=self.timeout,
                    close_timeout=1,
                    ping_interval=None,
                    max_size=1024 * 1024,
                ) as websocket:
                    heartbeat = asyncio.create_task(self._heartbeat(websocket))
                    try:
                        pending: deque[dict[str, Any]] = deque()
                        stamp = str(int(time.time() * 1000))
                        signature = hmac.new(
                            self.credentials.api_secret.encode(),
                            (self.credentials.api_key + stamp).encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        await websocket.send(
                            json.dumps(
                                {
                                    "method": "login",
                                    "param": {
                                        "apiKey": self.credentials.api_key,
                                        "reqTime": stamp,
                                        "signature": signature,
                                        "subscribe": False,
                                    },
                                }
                            )
                        )
                        await self._expect(websocket, "rs.login", pending)
                        await websocket.send(
                            json.dumps(
                                {
                                    "method": "personal.filter",
                                    "param": {"filters": [{"filter": "position"}]},
                                }
                            )
                        )
                        await self._expect(websocket, "rs.personal.filter", pending)
                        # Subscribe before the snapshot; changes during REST wait in the socket.
                        details = await self.load_details()
                        book = PositionBook(await self.load_positions(), details)
                        await self._subscribe_prices(websocket, book.symbols, set(), pending)
                        yield PositionsEvent("live", book.snapshot())
                        delay = self.retry_delay
                        while True:
                            message = (
                                pending.popleft()
                                if pending
                                else decode_message(
                                    await asyncio.wait_for(websocket.recv(), self.stale_after)
                                )
                            )
                            self._check_response(message)
                            previous = book.symbols
                            if book.apply(message):
                                await self._subscribe_prices(
                                    websocket,
                                    book.symbols,
                                    previous,
                                    pending,
                                )
                                yield PositionsEvent("live", book.snapshot())
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError, WebSocketException, OSError):
                            await heartbeat
            except (
                WebSocketException,
                OSError,
                TimeoutError,
                MEXCError,
                ValueError,
                KeyError,
                TypeError,
                EOFError,
            ) as exc:
                logger.warning(
                    "Positions WebSocket interrupted (%s); reconnecting",
                    type(exc).__name__,
                )
                retry = True
                yield PositionsEvent("reconnecting")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
