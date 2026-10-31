import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal

import httpx
import pytest

from perpetui.client import MEXCClient, MEXCError
from perpetui.credentials import Credentials


def response(data):
    return httpx.Response(200, json={"success": True, "code": 0, "data": data})


def position(symbol="ETH_USDT", **kwargs):
    return {
        "positionId": 1,
        "symbol": symbol,
        "positionType": 1,
        "state": 1,
        "holdVol": 120,
        "holdAvgPrice": "3000",
        "liquidatePrice": "2800",
        "leverage": 10,
        "im": "360",
        "openType": 1,
        **kwargs,
    }


async def test_signed_positions_preserve_order_and_use_contract_size():
    requests = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/ping"):
            return response(int(time.time() * 1000))
        if path.endswith("/open_positions"):
            stamp = request.headers["Request-Time"]
            expected = hmac.new(b"secret", ("key" + stamp).encode(), hashlib.sha256).hexdigest()
            assert request.headers["Signature"] == expected
            assert request.headers["ApiKey"] == "key"
            assert request.headers["Recv-Window"] == "10"
            return response(
                [
                    position(),
                    position(
                        "BTC_USDT",
                        positionId=2,
                        positionType=2,
                        holdVol=10,
                        holdAvgPrice="97000",
                        im="194",
                    ),
                    position("SOL_USDT", positionId=3, holdVol=0),
                    position("XRP_USDT", positionId=4, state=3),
                ]
            )
        if path.endswith("/ticker"):
            return response(
                [
                    {"symbol": "ETH_USDT", "fairPrice": "3100"},
                    {"symbol": "BTC_USDT", "fairPrice": "96000"},
                ]
            )
        if path.endswith("/detail"):
            return response(
                [
                    {"symbol": "ETH_USDT", "contractSize": ".01", "settleCoin": "USDT"},
                    {"symbol": "BTC_USDT", "contractSize": ".001", "settleCoin": "USDT"},
                ]
            )
        pytest.fail(f"Unexpected request {request.url}")

    client = MEXCClient(Credentials("key", "secret"), transport=httpx.MockTransport(handle))
    try:
        snapshot = await client.positions()
        assert [p.symbol for p in snapshot.items] == ["ETH_USDT", "BTC_USDT"]
        assert snapshot.items[0].base_size == Decimal("1.20")
        assert snapshot.items[0].pnl == Decimal("120")
        assert snapshot.items[1].pnl == Decimal("10")
        assert snapshot.items[0].roe == Decimal("100") / 3
        await client.positions()
        assert sum(r.url.path.endswith("/detail") for r in requests) == 1
        assert sum(r.url.path.endswith("/ping") for r in requests) == 1
        assert all(r.method == "GET" for r in requests)
    finally:
        await client.close()


async def test_enrichment_failure_keeps_open_positions_and_unknown_pnl():
    def handle(request):
        if request.url.path.endswith("/ping"):
            return response(int(time.time() * 1000))
        if request.url.path.endswith("/open_positions"):
            return response([position()])
        return httpx.Response(503, text="unavailable")

    client = MEXCClient(Credentials("key", "secret"), transport=httpx.MockTransport(handle))
    try:
        snapshot = await client.positions()
        assert len(snapshot.items) == 1
        assert snapshot.items[0].pnl is None
        assert "ETH_USDT" in snapshot.warnings[0]
    finally:
        await client.close()


def market_response(request, *, malformed=False):
    if "/kline/" in request.url.path:
        assert request.url.params["interval"] == "Min15"
        assert int(request.url.params["end"]) - int(request.url.params["start"]) == 900 * 160
        return response(
            {
                "time": [100, 200],
                "open": [10, 11],
                "high": [12, 13],
                "low": [9, 10],
                "close": [11] if malformed else [11, 12],
                "vol": [5, 6],
            }
        )
    return response(
        {
            "symbol": "BTC_USDT",
            "lastPrice": "12.12345678",
            "fairPrice": "12.1",
            "riseFallRate": ".05",
            "high24Price": 13,
            "lower24Price": 9,
            "amount24": 1000,
        }
    )


async def test_market_parses_candles_and_decimal_ticker():
    client = MEXCClient(transport=httpx.MockTransport(market_response))
    try:
        market = await client.market("BTC_USDT", "15m")
        assert market.last == Decimal("12.12345678")
        assert market.change == Decimal(5)
        assert len(market.candles) == 2
        assert market.candles[1].close == Decimal(12)
        assert (await client.positions()).items == []
    finally:
        await client.close()


async def test_history_requests_older_bars_and_filters_before_the_cursor():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.params["interval"] == "Min15"
        assert int(request.url.params["end"]) - int(request.url.params["start"]) == 900 * 3
        return response(
            {
                "time": [100, 200, 300],
                "open": [10, 11, 12],
                "high": [12, 13, 14],
                "low": [9, 10, 11],
                "close": [11, 12, 13],
                "vol": [5, 6, 7],
            }
        )

    client = MEXCClient(transport=httpx.MockTransport(handle))
    try:
        bars = await client.history("BTC_USDT", "15m", 250, 3)
        assert [bar.time for bar in bars] == [100, 200]
        assert bars[-1].close == Decimal(12)
        assert requests[0].url.params["end"] == "249"
    finally:
        await client.close()


async def test_malformed_candles_are_an_error_not_an_empty_position_state():
    client = MEXCClient(
        transport=httpx.MockTransport(lambda request: market_response(request, malformed=True))
    )
    try:
        with pytest.raises(MEXCError, match="Invalid or empty"):
            await client.market("BTC_USDT", "15m")
    finally:
        await client.close()


@pytest.mark.parametrize("payload", [[], {"code": 401, "message": "SECRET"}, {"data": None}])
async def test_bad_api_responses_do_not_expose_response_body(payload):
    client = MEXCClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=json.dumps(payload)))
    )
    try:
        with pytest.raises(MEXCError) as error:
            await client.market("BTC_USDT", "15m")
        assert "SECRET" not in str(error.value)
    finally:
        await client.close()


async def test_symbols_are_active_supported_contracts_and_share_metadata_cache():
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/v1/contract/detail"
        assert "ApiKey" not in request.headers
        await asyncio.sleep(0.01)
        return response(
            [
                {"symbol": "ZEC_USDT", "state": 0},
                {"symbol": "BTC_USDT", "state": "0"},
                {"symbol": "OLD_USDT", "state": 3},
                {"symbol": "PAUSED_USDT", "state": 4},
                {"symbol": "UNSUPPORTED_EUR", "state": 0},
            ]
        )

    client = MEXCClient(transport=httpx.MockTransport(handle))
    try:
        symbols, metadata = await asyncio.gather(client.symbols(), client._contract_details())
        assert symbols == ["BTC_USDT", "ZEC_USDT"]
        assert "OLD_USDT" in metadata
        assert await client.symbols() == symbols
        assert calls == 1
    finally:
        await client.close()


@pytest.mark.parametrize("payload", [{}, [None], [{}], [{"symbol": 42}]])
async def test_malformed_contract_metadata_is_a_recoverable_error(payload):
    client = MEXCClient(transport=httpx.MockTransport(lambda _: response(payload)))
    try:
        with pytest.raises(MEXCError, match="Invalid contract metadata"):
            await client.symbols()
    finally:
        await client.close()


@pytest.mark.parametrize(
    "authenticated,streaming,expected",
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
async def test_private_stream_requires_credentials_and_respects_no_websocket(
    authenticated,
    streaming,
    expected,
):
    client = MEXCClient(
        Credentials("key", "secret") if authenticated else None, streaming=streaming
    )
    try:
        assert client.position_streaming == expected
        if not authenticated:
            with pytest.raises(MEXCError, match="credentials required"):
                client.stream_positions()
    finally:
        await client.close()
