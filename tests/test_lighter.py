import asyncio
import json
from contextlib import aclosing
from decimal import Decimal

import httpx
import pytest
from websockets.asyncio.server import serve

from perpetui.errors import ExchangeError
from perpetui.exchanges.lighter.account import LighterAccount
from perpetui.exchanges.lighter.client import LighterClient
from perpetui.exchanges.lighter.parser import apply_market, normalize_lighter_symbol

DETAILS = [
    {
        "symbol": "BTC",
        "market_id": 0,
        "market_type": "perp",
        "status": "active",
        "mark_price": "110",
        "last_trade_price": 111,
        "daily_price_change": 1.5,
        "daily_price_high": 120,
        "daily_price_low": 90,
        "daily_quote_token_volume": 12345,
        "min_initial_margin_fraction": 200,
        "market_config": {"hidden": False},
    },
    {
        "symbol": "ETH",
        "market_id": 1,
        "market_type": "perp",
        "status": "inactive",
        "mark_price": "120",
        "last_trade_price": 121,
        "daily_price_change": 0,
        "daily_price_high": 130,
        "daily_price_low": 100,
        "daily_quote_token_volume": 1,
        "min_initial_margin_fraction": 200,
        "market_config": {"hidden": False},
    },
    {
        "symbol": "SOL",
        "market_id": 2,
        "market_type": "perp",
        "status": "active",
        "mark_price": "100",
        "last_trade_price": 100,
        "daily_price_change": 0,
        "daily_price_high": 110,
        "daily_price_low": 90,
        "daily_quote_token_volume": 1,
        "min_initial_margin_fraction": 400,
        "market_config": {"hidden": True},
    },
    {
        "symbol": "HYPE",
        "market_id": 3,
        "market_type": "perp",
        "status": "active",
        "mark_price": "40",
        "last_trade_price": 41,
        "daily_price_change": -2,
        "daily_price_high": 45,
        "daily_price_low": 35,
        "daily_quote_token_volume": 99,
        "min_initial_margin_fraction": 500,
        "market_config": {"hidden": False},
    },
]


def candle(stamp=900_000, volume="1.0", closing="101"):
    return {"t": stamp, "o": 100, "h": 120, "l": 80, "c": closing, "v": volume, "V": 100}


def position(symbol, size, sign=1, entry="100", value="55", pnl="5", fraction="2.00", mode=0):
    return {
        "market_id": next(row["market_id"] for row in DETAILS if row["symbol"] == symbol),
        "symbol": symbol,
        "initial_margin_fraction": fraction,
        "sign": sign,
        "position": size,
        "avg_entry_price": entry,
        "position_value": value,
        "unrealized_pnl": pnl,
        "liquidation_price": "80",
        "margin_mode": mode,
        "allocated_margin": "0",
    }


class API:
    def __init__(self):
        self.requests = []
        self.position_rows = [
            position("BTC", "0.5"),
            position("ETH", "-0.25", -1, "120", "30", "-1", "4.00", 1),
            position("SOL", "0"),
        ]

    def __call__(self, request):
        assert request.method == "GET"
        params = dict(request.url.params)
        self.requests.append((request.url.path, params))
        if request.url.path == "/api/v1/orderBookDetails":
            if params.get("market_id") == "0":
                data = {"code": 200, "order_book_details": [DETAILS[0]]}
            else:
                assert params == {"filter": "perp"}
                data = {"code": 200, "order_book_details": DETAILS}
        elif request.url.path == "/api/v1/candles":
            assert params["market_id"] == "0"
            assert params["resolution"] == "15m"
            assert params["count_back"] == "160"
            assert int(params["end_timestamp"]) - int(params["start_timestamp"]) == 900 * 160_000
            data = {"code": 200, "r": "15m", "c": [candle()]}
        elif request.url.path == "/api/v1/account":
            assert params["by"] == "index" and params["value"] == "123"
            data = {
                "code": 200,
                "accounts": [{"account_index": 123, "positions": self.position_rows}],
            }
        else:
            pytest.fail(f"Unexpected endpoint {request.url.path}")
        return httpx.Response(200, json=data)


@pytest.mark.parametrize(
    "value,expected", [("btc", "BTC"), ("ETH/USDC", "ETH"), ("1000pepe", "1000PEPE")]
)
def test_native_symbol_names(value, expected):
    assert normalize_lighter_symbol(value) == expected


@pytest.mark.parametrize("value", ["", "../BTC", "BTC?x=y", "ETH/USDC/../x"])
def test_invalid_symbol_names(value):
    with pytest.raises(ValueError):
        normalize_lighter_symbol(value)


async def test_rest_search_positions_precision_and_shared_metadata_cache():
    api = API()
    client = LighterClient(LighterAccount(123, "main"), transport=httpx.MockTransport(api))
    try:
        symbols, positions, market = await asyncio.gather(
            client.symbols(), client.positions(), client.market("btc", "15m")
        )
        assert symbols == ["BTC", "HYPE"]
        long, short = positions.items
        assert long.contracts == Decimal(".5") and short.contracts == Decimal(".25")
        assert long.side == "LONG" and short.side == "SHORT"
        assert long.leverage == 50 and short.leverage == 25
        assert long.margin == Decimal("1.10000") and short.margin == Decimal("1.2000")
        assert long.mark == Decimal("110") and short.mark == Decimal("120")
        assert long.settlement == short.settlement == "USDC"
        assert long.pnl == Decimal("5") and short.pnl == Decimal("-1")
        assert round(long.roe, 4) == Decimal("454.5455")
        assert market.last == 111 and market.mark == 110 and market.change == Decimal("1.5")
        assert market.high == 120 and market.low == 90 and market.amount == 12345
        assert market.candles[0].time == 900
        assert sum(path == "/api/v1/orderBookDetails" for path, _ in api.requests) == 2
    finally:
        await client.close()


async def test_history_requests_older_bars_and_filters_before_the_cursor():
    class HistoryAPI(API):
        def __call__(self, request):
            if request.url.path == "/api/v1/candles":
                params = dict(request.url.params)
                assert params["market_id"] == "0"
                assert params["resolution"] == "15m"
                assert params["count_back"] == "2"
                return httpx.Response(
                    200, json={"code": 200, "c": [candle(900_000), candle(1_800_000)]}
                )
            return super().__call__(request)

    client = LighterClient(transport=httpx.MockTransport(HistoryAPI()))
    try:
        bars = await client.history("BTC", "15m", 1800, 2)
        assert [bar.time for bar in bars] == [900]
    finally:
        await client.close()


async def test_public_mode_never_reads_accounts_and_testnet_endpoints_are_separate():
    api = API()
    client = LighterClient(testnet=True, transport=httpx.MockTransport(api))
    try:
        assert (await client.positions()).items == []
        assert not api.requests
        assert "testnet.zklighter.elliot.ai" in str(client._http.base_url)
        assert client.websocket_url == "wss://testnet.zklighter.elliot.ai/stream"
        assert not client.position_streaming
    finally:
        await client.close()


async def test_candle_and_market_stats_updates_are_cumulative():
    client = LighterClient(transport=httpx.MockTransport(API()))
    try:
        seed = await client.market("BTC", "15m")
    finally:
        await client.close()
    first = apply_market(
        seed,
        {
            "type": "update/candle",
            "channel": "candle:0:15m",
            "candles": [candle(volume="2", closing="105")],
        },
    )
    old = apply_market(
        first,
        {
            "type": "update/candle",
            "channel": "candle:0:15m",
            "candles": [candle(volume="1", closing="102")],
        },
    )
    assert old is first
    updated = apply_market(
        first,
        {
            "type": "update/market_stats",
            "channel": "market_stats:0",
            "market_stats": {
                "symbol": "BTC",
                "mark_price": "103",
                "last_trade_price": "104",
                "daily_price_change": 2.5,
                "daily_price_high": 130,
                "daily_price_low": 85,
                "daily_quote_token_volume": 13000,
            },
        },
    )
    assert updated.last == 104 and updated.mark == 103 and updated.change == Decimal("2.5")
    assert updated.high == 130 and updated.low == 85 and updated.amount == 13000
    assert updated.candles[-1].volume == 2


@pytest.mark.parametrize(
    "bad",
    [
        httpx.Response(503, text="SECRET"),
        httpx.Response(200, json={"code": 500, "message": "SECRET"}),
        httpx.Response(200, text="SECRET"),
    ],
)
async def test_api_errors_do_not_expose_response_payload(bad):
    client = LighterClient(transport=httpx.MockTransport(lambda _: bad))
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
        if command["type"] == "ping":
            await ws.send('{"type":"pong"}')
        else:
            assert command["type"] == "subscribe"
            seen.append(command["channel"])
    return seen


async def test_market_websocket_uses_native_channels_and_repairs_gaps():
    api = API()

    async def handler(ws):
        await ws.send('{"type":"connected"}')
        assert await subscriptions(ws, 2) == ["candle/0/15m", "market_stats/0"]
        await ws.send(
            json.dumps(
                {
                    "type": "update/candle",
                    "channel": "candle:0:15m",
                    "candles": [candle(volume="2", closing="105")],
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "type": "update/candle",
                    "channel": "candle:0:15m",
                    "candles": [candle(3_600_000, volume="1")],
                }
            )
        )
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        client = LighterClient(
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
            assert sum(path == "/api/v1/candles" for path, _ in api.requests) == 2
        finally:
            await client.close()


async def test_position_websocket_replaces_snapshots_and_removes_closed_rows():
    api = API()

    async def handler(ws):
        await ws.send('{"type":"connected"}')
        assert await subscriptions(ws, 1) == ["account_all_positions/123"]
        for rows in (
            [position("BTC", "0.75"), position("ETH", "-0.25", -1, "120", "30", "-1", "4.00", 1)],
            [position("BTC", "0"), position("ETH", "-0.25", -1, "120", "30", "-1", "4.00", 1)],
        ):
            await ws.send(
                json.dumps(
                    {
                        "type": "update/account_all_positions",
                        "channel": "account_all_positions:123",
                        "positions": {str(row["market_id"]): row for row in rows},
                    }
                )
            )
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        client = LighterClient(
            LighterAccount(123),
            transport=httpx.MockTransport(api),
            websocket_url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        try:
            async with aclosing(client.stream_positions()) as events:
                async with asyncio.timeout(3):
                    await anext(events)
                    first = await anext(events)
                    closed = await anext(events)
                    assert [p.symbol for p in first.positions.items] == ["BTC", "ETH"]
                    assert first.positions.items[0].contracts == Decimal(".75")
                    assert [p.symbol for p in closed.positions.items] == ["ETH"]
        finally:
            await client.close()
