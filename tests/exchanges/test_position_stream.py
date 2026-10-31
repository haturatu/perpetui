import asyncio
import hashlib
import hmac
import json
from contextlib import aclosing
from dataclasses import replace
from decimal import Decimal

import pytest
from websockets.asyncio.server import serve

from perpetui.domain.models import Positions
from perpetui.exchanges.demo import DemoClient
from perpetui.exchanges.mexc.account import Credentials
from perpetui.exchanges.mexc.positions import PositionBook, parse_position
from perpetui.exchanges.mexc.stream import PositionsStream

CREDENTIALS = Credentials("test-key", "test-secret")
DETAILS = {
    "ETH_USDT": {"contractSize": ".01", "settleCoin": "USDT"},
    "BTC_USDT": {"contractSize": ".001", "settleCoin": "USDT"},
    "SOL_USDT": {"contractSize": ".1", "settleCoin": "USDT"},
}


def position_message(position_id="1", **kwargs):
    return {"channel": "push.personal.position", "data": {"positionId": position_id, **kwargs}}


def fair_message(symbol="ETH_USDT", price="3600", stamp=100):
    return {"channel": "push.fair.price", "data": {"symbol": symbol, "price": price}, "ts": stamp}


async def test_fair_prices_revalue_both_hedged_sides_and_ignore_old_prices():
    long = (await DemoClient().positions()).items[0]
    short = replace(long, id="hedge", side="SHORT")
    book = PositionBook(Positions([long, short]), DETAILS)
    assert book.apply(fair_message())
    first, second = book.snapshot().items
    assert first.mark == second.mark == Decimal(3600)
    assert first.pnl == -second.pnl == Decimal("152.64")
    assert first.roe == first.pnl / first.margin * 100
    assert not book.apply(fair_message(price="3500", stamp=99))
    assert not book.apply(fair_message("UNKNOWN_USDT"))
    assert book.snapshot().items[0].mark == 3600


async def test_new_resized_and_closed_positions_preserve_marks_and_reject_late_events():
    book = PositionBook(Positions([]), DETAILS)
    opened = position_message(
        symbol="SOL_USDT",
        holdVol=10,
        positionType=2,
        holdAvgPrice="180",
        im="18",
        leverage=10,
        openType=2,
        version=1,
        updateTime=100,
    )
    assert book.apply(opened)
    assert book.symbols == {"SOL_USDT"}
    assert book.snapshot().warnings
    assert book.apply(fair_message("SOL_USDT", "170", 200))
    assert not book.snapshot().warnings
    assert book.snapshot().items[0].pnl == Decimal(10)
    assert book.apply(position_message(holdVol=5, im=0, version=2, updateTime=300))
    resized = book.snapshot().items[0]
    assert resized.side == "SHORT" and resized.margin_mode == "Cross"
    assert resized.mark == 170 and resized.pnl == 5 and resized.margin == 0
    assert resized.roe is None
    assert not book.apply(opened)
    assert book.apply(position_message(state=3, version=3, updateTime=400))
    assert not book.symbols
    assert not book.apply(opened)
    assert not book.snapshot().items


async def test_snapshot_versions_prevent_buffered_messages_from_rewinding_rest():
    position = replace((await DemoClient().positions()).items[0], version=5, updated_at=500)
    book = PositionBook(Positions([position]), DETAILS)
    assert not book.apply(position_message(holdVol=1, version=4, updateTime=600))
    assert not book.apply(position_message(holdVol=1, updateTime=400))
    assert book.snapshot().items[0].contracts == position.contracts


async def test_metadata_and_fair_stream_recover_a_snapshot_without_valuation():
    position = replace((await DemoClient().positions()).items[0], mark=None, contract_size=None)
    book = PositionBook(Positions([position]), DETAILS)
    assert book.snapshot().warnings
    assert book.apply(fair_message())
    assert book.snapshot().items[0].pnl is not None
    assert not book.snapshot().warnings


def test_shared_parser_keeps_full_precision_and_explicit_zero_margin():
    position = parse_position(
        {
            "positionId": 1,
            "symbol": "ETH_USDT",
            "positionType": 1,
            "holdVol": 10,
            "holdAvgPrice": "3000.12",
            "holdAvgPriceFullyScale": "3000.1234567890123456789",
            "im": 0,
            "oim": 30,
            "version": 3,
            "updateTime": 42,
        }
    )
    assert position.entry == Decimal("3000.1234567890123456789")
    assert position.margin == 0
    assert (position.version, position.updated_at) == (3, 42)


@pytest.mark.parametrize("value", ["NaN", "-1", "0", "Infinity"])
async def test_invalid_fair_prices_are_rejected(value):
    book = PositionBook(await DemoClient().positions(), DETAILS)
    with pytest.raises(ValueError):
        book.apply(fair_message(price=value))


async def next_command(websocket):
    while True:
        command = json.loads(await websocket.recv())
        if command["method"] == "ping":
            await websocket.send('{"channel":"pong"}')
        else:
            return command


async def authenticate(websocket):
    login = await next_command(websocket)
    assert login["method"] == "login"
    params = login["param"]
    expected = hmac.new(
        b"test-secret",
        ("test-key" + params["reqTime"]).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert params["signature"] == expected
    assert params["apiKey"] == "test-key" and params["subscribe"] is False
    await websocket.send('{"channel":"rs.login","data":"success"}')
    command = await next_command(websocket)
    assert command == {"method": "personal.filter", "param": {"filters": [{"filter": "position"}]}}
    await websocket.send('{"channel":"rs.personal.filter","data":"success"}')


async def acknowledge(websocket, method, symbol):
    command = await next_command(websocket)
    assert command["method"] == method
    assert command["param"] == {"symbol": symbol}
    await websocket.send(json.dumps({"channel": "rs." + method, "data": "success"}))


async def details():
    return DETAILS


async def test_transport_authentication_dynamic_subscriptions_heartbeat_and_cleanup():
    eth = (await DemoClient().positions()).items[0]
    disconnected = asyncio.Event()

    async def snapshot():
        return Positions([eth])

    async def handler(websocket):
        try:
            await authenticate(websocket)
            await acknowledge(websocket, "sub.fair.price", "ETH_USDT")
            await websocket.send(json.dumps(fair_message()))
            await websocket.send(
                json.dumps(
                    position_message(
                        "new",
                        symbol="SOL_USDT",
                        holdVol=10,
                        positionType=1,
                        holdAvgPrice="180",
                        im="18",
                        version=1,
                        updateTime=300,
                    )
                )
            )
            await acknowledge(websocket, "sub.fair.price", "SOL_USDT")
            await websocket.send(json.dumps(fair_message("SOL_USDT", "190", 400)))
            await websocket.send(json.dumps(position_message(state=3, version=1, updateTime=500)))
            await acknowledge(websocket, "unsub.fair.price", "ETH_USDT")
            await websocket.wait_closed()
        finally:
            disconnected.set()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = PositionsStream(
            CREDENTIALS,
            snapshot,
            details,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        seen = []
        async with aclosing(stream.watch()) as events:
            async with asyncio.timeout(3):
                async for event in events:
                    seen.append(event)
                    if event.positions and [p.id for p in event.positions.items] == ["new"]:
                        break
        await asyncio.wait_for(disconnected.wait(), 1)
        assert seen[0].state == "connecting"
        assert seen[-1].positions.items[0].pnl == 10
        assert seen[-1].positions.items[0].mark == 190


async def test_reconnect_resnapshots_changes_missed_while_disconnected():
    positions = (await DemoClient().positions()).items
    loads = 0
    connections = 0

    async def snapshot():
        nonlocal loads
        loads += 1
        return Positions([positions[0 if loads == 1 else 1]])

    async def handler(websocket):
        nonlocal connections
        connections += 1
        await authenticate(websocket)
        symbol = "ETH_USDT" if connections == 1 else "BTC_USDT"
        await acknowledge(websocket, "sub.fair.price", symbol)
        if connections == 1:
            await websocket.close(code=1012)
        else:
            await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = PositionsStream(
            CREDENTIALS,
            snapshot,
            details,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            retry_delay=0.01,
        )
        seen = []
        async with aclosing(stream.watch()) as events:
            async with asyncio.timeout(3):
                async for event in events:
                    seen.append(event)
                    if event.positions and event.positions.items[0].symbol == "BTC_USDT":
                        break
        assert loads == connections == 2
        assert any(event.state == "reconnecting" for event in seen)


async def test_position_events_during_rest_snapshot_are_buffered_and_applied():
    eth = (await DemoClient().positions()).items[0]
    started = asyncio.Event()
    release = asyncio.Event()

    async def snapshot():
        started.set()
        await release.wait()
        return Positions([eth])

    async def handler(websocket):
        await authenticate(websocket)
        await started.wait()
        await websocket.send(json.dumps(position_message(holdVol=12, version=1, updateTime=200)))
        release.set()
        await acknowledge(websocket, "sub.fair.price", "ETH_USDT")
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = PositionsStream(
            CREDENTIALS,
            snapshot,
            details,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        async with aclosing(stream.watch()) as events:
            async with asyncio.timeout(3):
                await anext(events)
                assert (await anext(events)).positions.items[0].contracts == 120
                assert (await anext(events)).positions.items[0].contracts == 12


async def test_empty_portfolio_still_receives_new_position():
    async def snapshot():
        return Positions([])

    async def handler(websocket):
        await authenticate(websocket)
        await websocket.send(
            json.dumps(
                position_message(
                    symbol="SOL_USDT",
                    holdVol=10,
                    positionType=1,
                    holdAvgPrice=180,
                    im=18,
                )
            )
        )
        await acknowledge(websocket, "sub.fair.price", "SOL_USDT")
        await websocket.send(json.dumps(fair_message("SOL_USDT", "190")))
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = PositionsStream(
            CREDENTIALS,
            snapshot,
            details,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        async with aclosing(stream.watch()) as events:
            async with asyncio.timeout(3):
                await anext(events)
                assert not (await anext(events)).positions.items
                assert (await anext(events)).positions.items[0].mark is None
                assert (await anext(events)).positions.items[0].pnl == 10


@pytest.mark.parametrize("mode", ["rejected", "no_ack", "silent"])
async def test_rejections_and_stalls_reconnect_without_logging_private_payload(mode, caplog):
    async def handler(websocket):
        if mode == "rejected":
            await next_command(websocket)
            await websocket.send('{"channel":"rs.error","data":"SECRET_SERVER_PAYLOAD"}')
        elif mode == "silent":
            await authenticate(websocket)
        await websocket.wait_closed()

    async def snapshot():
        return Positions([])

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = PositionsStream(
            CREDENTIALS,
            snapshot,
            details,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            timeout=0.05,
            heartbeat=1,
            stale_after=0.05,
        )
        async with aclosing(stream.watch()) as events:
            async with asyncio.timeout(3):
                async for event in events:
                    if event.state == "reconnecting":
                        break
        assert "Positions WebSocket interrupted" in caplog.text
        for secret in ("SECRET_SERVER_PAYLOAD", "test-key", "test-secret"):
            assert secret not in caplog.text


async def test_idle_positions_with_heartbeat_pongs_do_not_reconnect():
    async def snapshot():
        return Positions([])

    async def handler(websocket):
        await authenticate(websocket)
        async for raw in websocket:
            assert json.loads(raw)["method"] == "ping"
            await websocket.send('{"channel":"pong"}')

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = PositionsStream(
            CREDENTIALS,
            snapshot,
            details,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            heartbeat=0.01,
            stale_after=0.06,
        )
        async with aclosing(stream.watch()) as events:
            await anext(events)
            assert (await anext(events)).state == "live"
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(anext(events), 0.15)
