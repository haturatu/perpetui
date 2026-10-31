import asyncio
import json
from contextlib import aclosing
from decimal import Decimal

import httpx
import pytest
from websockets.asyncio.server import serve

from perpetui.errors import ExchangeError
from perpetui.exchanges.hyperliquid.account import HLAccount
from perpetui.exchanges.hyperliquid.client import HyperliquidClient
from perpetui.exchanges.hyperliquid.parser import apply_market, normalize_hl_symbol

ADDRESS = "0x" + "1" * 40
METAS = [
    {
        "universe": [{"name": "BTC"}, {"name": "kPEPE"}, {"name": "OLD", "isDelisted": True}],
        "collateralToken": 0,
    },
    {"universe": [{"name": "xyz:CL"}], "collateralToken": 1},
]


def state(coin="BTC", size="0.5", stamp=100):
    return {
        "time": stamp,
        "assetPositions": [
            {
                "position": {
                    "coin": coin,
                    "szi": size,
                    "entryPx": "100",
                    "positionValue": str(abs(Decimal(size)) * 110),
                    "unrealizedPnl": "5.123456789",
                    "returnOnEquity": ".123456789",
                    "marginUsed": "10",
                    "leverage": {"type": "cross", "value": 5},
                    "liquidationPx": None,
                }
            }
        ]
        if Decimal(size)
        else [],
    }


def candle(stamp=900_000, volume="1.0000000001", closing="101"):
    return {
        "s": "BTC",
        "i": "15m",
        "t": stamp,
        "T": stamp + 899999,
        "o": "100",
        "h": "120",
        "l": "80",
        "c": closing,
        "v": volume,
    }


class API:
    def __init__(self):
        self.requests = []
        self.native_state = state()

    def __call__(self, request):
        assert request.method == "POST" and request.url.path == "/info"
        assert "Authorization" not in request.headers
        payload = json.loads(request.content)
        self.requests.append(payload)
        kind = payload["type"]
        if kind == "allPerpMetas":
            data = METAS
        elif kind == "perpDexs":
            data = [None, {"name": "xyz"}]
        elif kind == "spotMeta":
            data = {"tokens": [{"index": 0, "name": "USDC"}, {"index": 1, "name": "USDH"}]}
        elif kind == "clearinghouseState":
            assert payload["user"] == ADDRESS
            data = self.native_state if payload["dex"] == "" else state("xyz:CL", "-.25")
        elif kind == "candleSnapshot":
            assert payload["req"]["coin"] == "BTC"
            assert payload["req"]["endTime"] - payload["req"]["startTime"] == 900 * 160_000
            data = [candle()]
        elif kind == "metaAndAssetCtxs":
            assert payload["dex"] == ""
            data = [METAS[0], [{"markPx": "110", "prevDayPx": "100", "dayNtlVlm": "12345"}] * 3]
        else:
            pytest.fail(f"Unexpected read endpoint {kind}")
        return httpx.Response(200, json=data)


@pytest.mark.parametrize(
    "value,expected",
    [("btc", "BTC"), ("BTC/USDC", "BTC"), ("xyz:cl", "xyz:CL"), ("kpepe", "kPEPE")],
)
def test_native_symbol_names(value, expected):
    assert normalize_hl_symbol(value) == expected


@pytest.mark.parametrize("value", ["", "../BTC", "BTC?x=y", "xyz:../CL"])
def test_invalid_symbol_names(value):
    with pytest.raises(ValueError):
        normalize_hl_symbol(value)


async def test_rest_search_positions_precision_collateral_and_shared_metadata_cache():
    api = API()
    client = HyperliquidClient(HLAccount(ADDRESS), transport=httpx.MockTransport(api))
    try:
        symbols, positions, market = await asyncio.gather(
            client.symbols(), client.positions(), client.market("btc", "15m")
        )
        assert symbols == ["BTC", "kPEPE", "xyz:CL"]
        long, short = positions.items
        assert long.contracts == Decimal(".5") and short.contracts == Decimal(".25")
        assert long.side == "LONG" and short.side == "SHORT"
        assert long.settlement == "USDC" and short.settlement == "USDH"
        assert long.pnl == short.pnl == Decimal("5.123456789")
        assert long.roe == Decimal(
            "12.3456789"
        )  # Use exchange ROE, not marginUsed as its denominator.
        assert long.mark == 110 and long.liquidation == 0
        assert market.last == 101 and market.mark == 110 and market.change == 10
        assert market.high is None and market.low is None
        assert market.candles[0].volume == Decimal("1.0000000001")
        assert sum(r["type"] == "allPerpMetas" for r in api.requests) == 1
    finally:
        await client.close()


async def test_history_requests_older_bars_and_filters_before_the_cursor():
    class HistoryAPI(API):
        def __call__(self, request):
            payload = json.loads(request.content)
            if payload["type"] == "candleSnapshot":
                self.requests.append(payload)
                span = payload["req"]["endTime"] - payload["req"]["startTime"]
                assert span == (900 * 2 - 1) * 1000
                return httpx.Response(200, json=[candle(900_000), candle(1_800_000)])
            return super().__call__(request)

    api = HistoryAPI()
    client = HyperliquidClient(transport=httpx.MockTransport(api))
    try:
        bars = await client.history("BTC", "15m", 1800, 2)
        assert [bar.time for bar in bars] == [900]
    finally:
        await client.close()


async def test_public_mode_never_reads_accounts_and_testnet_endpoints_are_separate():
    api = API()
    client = HyperliquidClient(testnet=True, transport=httpx.MockTransport(api))
    try:
        assert (await client.positions()).items == []
        assert not api.requests
        assert "hyperliquid-testnet.xyz" in str(client._http.base_url)
        assert client.websocket_url == "wss://api.hyperliquid-testnet.xyz/ws"
        assert not client.position_streaming
    finally:
        await client.close()


async def test_candle_updates_are_cumulative_and_lagging_context_cannot_rewind_trade_price():
    client = HyperliquidClient(transport=httpx.MockTransport(API()))
    try:
        seed = await client.market("BTC", "15m")
    finally:
        await client.close()
    first = apply_market(seed, {"channel": "candle", "data": candle(volume="2", closing="105")})
    old = apply_market(first, {"channel": "candle", "data": candle(volume="1", closing="102")})
    assert old is first
    updated = apply_market(
        first,
        {
            "channel": "activeAssetCtx",
            "data": {
                "coin": "BTC",
                "ctx": {
                    "markPx": "103",
                    "prevDayPx": "100",
                    "dayNtlVlm": "13000",
                },
            },
        },
    )
    assert updated.last == 105 and updated.mark == 103
    assert updated.candles[-1].volume == 2


@pytest.mark.parametrize(
    "bad",
    [
        httpx.Response(503, text="SECRET"),
        httpx.Response(200, json={"error": "SECRET"}),
        httpx.Response(200, text="SECRET"),
    ],
)
async def test_api_errors_do_not_expose_response_payload(bad):
    client = HyperliquidClient(transport=httpx.MockTransport(lambda _: bad))
    try:
        with pytest.raises(ExchangeError) as error:
            await client.symbols()
        assert "SECRET" not in str(error.value)
    finally:
        await client.close()


async def subscriptions(ws, count):
    seen = []
    while len(seen) < count:
        command = json.loads(await ws.recv())
        if command["method"] == "ping":
            await ws.send('{"channel":"pong"}')
        else:
            assert command["method"] == "subscribe"
            seen.append(command["subscription"])
    return seen


async def test_market_websocket_uses_native_subscriptions_and_repairs_gaps():
    api = API()

    async def handler(ws):
        subs = await subscriptions(ws, 2)
        assert subs == [
            {"type": "candle", "coin": "BTC", "interval": "15m"},
            {"type": "activeAssetCtx", "coin": "BTC"},
        ]
        await ws.send(json.dumps({"channel": "candle", "data": candle(volume="2", closing="105")}))
        await ws.send(
            json.dumps({"channel": "candle", "data": candle(stamp=3_600_000, volume="1")})
        )
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        client = HyperliquidClient(
            transport=httpx.MockTransport(api),
            websocket_url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        try:
            seed = await client.market("BTC", "15m")
            async with aclosing(client.stream_market(seed)) as events:
                async with asyncio.timeout(3):
                    assert (await anext(events)).state == "connecting"
                    assert (await anext(events)).market.last == 105
                    repaired = await anext(events)
                    assert repaired.market.candles[-1].time == 900
            assert sum(r["type"] == "candleSnapshot" for r in api.requests) == 2
        finally:
            await client.close()


async def test_all_dex_positions_updates_reject_stale_snapshots_and_remove_closed_rows():
    api = API()

    async def handler(ws):
        assert await subscriptions(ws, 1) == [
            {"type": "allDexsClearinghouseState", "user": ADDRESS}
        ]
        for size, stamp in ((".75", 200), (".5", 150), ("0", 300)):
            await ws.send(
                json.dumps(
                    {
                        "channel": "allDexsClearinghouseState",
                        "data": {
                            "user": ADDRESS,
                            "clearinghouseStates": [
                                ["", state(size=size, stamp=stamp)],
                                ["xyz", state("xyz:CL", "-.25", stamp=stamp)],
                            ],
                        },
                    }
                )
            )
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        client = HyperliquidClient(
            HLAccount(ADDRESS),
            transport=httpx.MockTransport(api),
            websocket_url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        try:
            async with aclosing(client.stream_positions()) as events:
                async with asyncio.timeout(3):
                    await anext(events)
                    first, stale, closed = (
                        await anext(events),
                        await anext(events),
                        await anext(events),
                    )
                    assert (
                        first.positions.items[0].contracts
                        == stale.positions.items[0].contracts
                        == Decimal(".75")
                    )
                    assert [p.symbol for p in closed.positions.items] == ["xyz:CL"]
        finally:
            await client.close()


async def test_position_stream_reconnects_and_resyncs_rest_before_new_snapshot():
    api = API()
    connections = 0
    first_seen = asyncio.Event()

    async def handler(ws):
        nonlocal connections
        connections += 1
        await subscriptions(ws, 1)
        if connections == 1:
            await ws.send(
                json.dumps(
                    {
                        "channel": "allDexsClearinghouseState",
                        "data": {
                            "user": ADDRESS,
                            "clearinghouseStates": [
                                ["", state(stamp=200)],
                                ["xyz", state("xyz:CL", "-.25", stamp=200)],
                            ],
                        },
                    }
                )
            )
            await first_seen.wait()
            api.native_state = state(size="1", stamp=300)
            await ws.close(code=1012)
        else:
            await ws.send(
                json.dumps(
                    {
                        "channel": "allDexsClearinghouseState",
                        "data": {
                            "user": ADDRESS,
                            "clearinghouseStates": [
                                ["", api.native_state],
                                ["xyz", state("xyz:CL", "-.25", stamp=300)],
                            ],
                        },
                    }
                )
            )
            await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        client = HyperliquidClient(
            HLAccount(ADDRESS),
            transport=httpx.MockTransport(api),
            retry_delay=0.01,
            websocket_url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        seen = []
        try:
            async with aclosing(client.stream_positions()) as events:
                async with asyncio.timeout(3):
                    async for event in events:
                        seen.append(event)
                        if event.state == "live" and connections == 1:
                            first_seen.set()
                        if event.state == "live" and event.positions.items[0].contracts == 1:
                            break
            assert connections == 2
            assert any(e.state == "reconnecting" for e in seen)
        finally:
            await client.close()


async def test_idle_position_stream_accepts_heartbeat_without_spurious_reconnections():
    async def handler(ws):
        await subscriptions(ws, 1)
        await ws.send(
            json.dumps(
                {
                    "channel": "allDexsClearinghouseState",
                    "data": {
                        "user": ADDRESS,
                        "clearinghouseStates": [["", state(size="0")]],
                    },
                }
            )
        )
        async for raw in ws:
            assert json.loads(raw)["method"] == "ping"
            await ws.send('{"channel":"pong"}')

    async with serve(handler, "127.0.0.1", 0) as server:
        client = HyperliquidClient(
            HLAccount(ADDRESS),
            transport=httpx.MockTransport(API()),
            heartbeat=0.01,
            stale_after=0.04,
            websocket_url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        try:
            async with aclosing(client.stream_positions()) as events:
                await anext(events)
                assert (await anext(events)).state == "live"
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(anext(events), 0.15)
        finally:
            await client.close()
