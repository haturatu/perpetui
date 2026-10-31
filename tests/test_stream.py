import asyncio
import gzip
import json
from contextlib import aclosing
from dataclasses import replace
from decimal import Decimal

import pytest
from websockets.asyncio.server import serve

from perpetui.demo import DemoClient
from perpetui.models import INTERVALS
from perpetui.stream import HistoryGap, MarketStream, apply_message, decode_message


def candle_message(seed, *, timestamp=None, close=None, volume=42, **kwargs):
    bar = seed.candles[-1]
    closing = close if close is not None else bar.close + 1
    return {
        "channel": "push.kline",
        "symbol": seed.symbol,
        "data": {
            "symbol": seed.symbol,
            "interval": INTERVALS[seed.interval][0],
            "t": timestamp if timestamp is not None else bar.time,
            "o": str(bar.open),
            "c": str(closing),
            "h": str(max(bar.high, closing)),
            "l": str(min(bar.low, closing)),
            "q": volume,
            **kwargs,
        },
    }


async def test_cumulative_candles_replace_append_trim_and_ignore_old_buckets():
    seed = await DemoClient().market("BTC_USDT", "15m")
    first = apply_message(seed, candle_message(seed, volume=42))
    second = apply_message(first, candle_message(seed, close=seed.last + 2, volume=70))
    assert len(second.candles) == 160
    assert second.candles[-1].volume == Decimal(70)
    assert second.last == seed.last + 2
    next_bar = apply_message(
        second, candle_message(seed, timestamp=seed.candles[-1].time + 900, volume=3)
    )
    assert len(next_bar.candles) == 160
    assert next_bar.candles[-2] == second.candles[-1]
    assert next_bar.candles[-1].volume == 3
    assert apply_message(next_bar, candle_message(seed)) is next_bar
    assert apply_message(seed, candle_message(seed, symbol="ETH_USDT")) is seed
    assert apply_message(seed, candle_message(seed, interval="Min1")) is seed


async def test_ticker_updates_prices_without_changing_candle_history():
    seed = await DemoClient().market("BTC_USDT", "15m")
    updated = apply_message(
        seed,
        {
            "channel": "push.ticker",
            "data": {
                "symbol": seed.symbol,
                "lastPrice": "99000.01",
                "fairPrice": "98999.99",
                "riseFallRate": ".03",
                "high24Price": "100000",
                "lower24Price": "97000",
            },
        },
    )
    assert updated.last == Decimal("99000.01")
    assert updated.mark == Decimal("98999.99")
    assert updated.change == Decimal(3)
    assert updated.amount == seed.amount
    assert updated.candles is seed.candles


async def test_gap_and_malformed_candles_require_repair():
    seed = await DemoClient().market("BTC_USDT", "15m")
    with pytest.raises(HistoryGap):
        apply_message(seed, candle_message(seed, timestamp=seed.candles[-1].time + 1800))
    with pytest.raises(ValueError):
        apply_message(seed, candle_message(seed, c="NaN"))
    with pytest.raises(ValueError):
        apply_message(seed, candle_message(seed, q=-1))


def test_plain_binary_and_gzip_json_are_decoded_without_float_rounding():
    raw = b'{"channel":"push.ticker","data":{"lastPrice":0.1234567890123456789}}'
    for message in (raw, raw.decode(), gzip.compress(raw)):
        assert decode_message(message)["data"]["lastPrice"] == Decimal("0.1234567890123456789")
    with pytest.raises(ValueError):
        decode_message("[]")


async def subscribe(websocket, seed):
    subscriptions = [json.loads(await websocket.recv()) for _ in range(2)]
    assert subscriptions == [
        {
            "method": "sub.kline",
            "param": {
                "symbol": seed.symbol,
                "interval": INTERVALS[seed.interval][0],
            },
            "gzip": False,
        },
        {"method": "sub.ticker", "param": {"symbol": seed.symbol}, "gzip": False},
    ]
    assert json.loads(await websocket.recv()) == {"method": "ping"}


async def test_real_websocket_transport_subscriptions_heartbeat_and_updates():
    seed = await DemoClient().market("BTC_USDT", "15m")
    subscribed = asyncio.Event()

    async def handler(websocket):
        await subscribe(websocket, seed)
        subscribed.set()
        for volume in (42, 70):
            await websocket.send(json.dumps(candle_message(seed, volume=volume)))
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        stream = MarketStream(DemoClient().market, url=f"ws://127.0.0.1:{port}")
        async with aclosing(stream.watch(seed)) as events:
            async with asyncio.timeout(3):
                assert (await anext(events)).state == "connecting"
                first, second = await anext(events), await anext(events)
                assert first.state == second.state == "live"
                assert first.market.candles[-1].volume == 42
                assert second.market.candles[-1].volume == 70
        assert subscribed.is_set()


async def test_disconnect_reconnects_and_repairs_history_before_updates():
    seed = await DemoClient().market("BTC_USDT", "15m")
    new_bar = replace(seed.candles[-1], time=seed.candles[-1].time + 900)
    repaired = replace(seed, candles=seed.candles[1:] + [new_bar])
    connections, repairs = 0, 0

    async def load_market(symbol, interval):
        nonlocal repairs
        repairs += 1
        assert (symbol, interval) == (seed.symbol, seed.interval)
        return repaired

    async def handler(websocket):
        nonlocal connections
        connections += 1
        await subscribe(websocket, seed)
        if connections == 1:
            await websocket.send(json.dumps(candle_message(seed)))
            await websocket.close(code=1012)
        else:
            await websocket.send(json.dumps(candle_message(repaired, volume=90)))
            await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        stream = MarketStream(load_market, url=f"ws://127.0.0.1:{port}", retry_delay=0.01)
        seen = []
        async with aclosing(stream.watch(seed)) as events:
            async with asyncio.timeout(3):
                async for event in events:
                    seen.append(event)
                    if event.state == "live" and event.market.candles[-1].volume == 90:
                        break
        assert connections == 2
        assert repairs == 1
        assert any(event.state == "reconnecting" for event in seen)
        assert seen[-1].market.candles[-2].time == seed.candles[-1].time
        assert seen[-1].market.candles[-1].time == new_bar.time


async def test_missing_buckets_are_backfilled_without_synthetic_candles():
    seed = await DemoClient().market("BTC_USDT", "15m")
    new_bars = [replace(seed.candles[-1], time=seed.candles[-1].time + 900 * n) for n in (1, 2, 3)]
    repaired = replace(seed, candles=seed.candles[3:] + new_bars)
    repairs = 0

    async def load_market(symbol, interval):
        nonlocal repairs
        repairs += 1
        return repaired

    async def handler(websocket):
        await subscribe(websocket, seed)
        await websocket.send(json.dumps(candle_message(repaired)))
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = MarketStream(
            load_market, url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        )
        async with aclosing(stream.watch(seed)) as events:
            async with asyncio.timeout(3):
                await anext(events)
                event = await anext(events)
        assert repairs == 1
        assert event.market is repaired
        assert event.market.candles[-3:] == new_bars


async def test_pongs_alone_do_not_hide_a_stale_market_stream(caplog):
    seed = await DemoClient().market("BTC_USDT", "15m")

    async def handler(websocket):
        await subscribe(websocket, seed)
        await websocket.send('{"channel":"pong"}')
        async for _ in websocket:
            await websocket.send('{"channel":"pong"}')

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = MarketStream(
            DemoClient().market,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            heartbeat=0.01,
            stale_after=0.04,
            retry_delay=0.01,
        )
        async with aclosing(stream.watch(seed)) as events:
            async with asyncio.timeout(3):
                assert (await anext(events)).state == "connecting"
                assert (await anext(events)).state == "reconnecting"
        assert "TimeoutError" in caplog.text


async def test_subscription_rejection_falls_back_without_logging_server_payload(caplog):
    seed = await DemoClient().market("BTC_USDT", "15m")

    async def handler(websocket):
        await subscribe(websocket, seed)
        await websocket.send('{"channel":"rs.sub.kline","data":"SECRET_SERVER_PAYLOAD"}')
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = MarketStream(
            DemoClient().market,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        async with aclosing(stream.watch(seed)) as events:
            async with asyncio.timeout(3):
                assert (await anext(events)).state == "connecting"
                assert (await anext(events)).state == "reconnecting"
        assert "MEXCError" in caplog.text
        assert "SECRET_SERVER_PAYLOAD" not in caplog.text


async def test_older_timestamped_tickers_do_not_overwrite_newer_prices():
    seed = await DemoClient().market("BTC_USDT", "15m")

    async def handler(websocket):
        await subscribe(websocket, seed)
        for stamp, value in ((200, 98000), (100, 97000), (300, 99000)):
            await websocket.send(
                json.dumps(
                    {
                        "channel": "push.ticker",
                        "data": {
                            "symbol": seed.symbol,
                            "timestamp": stamp,
                            "lastPrice": value,
                        },
                    }
                )
            )
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = MarketStream(
            DemoClient().market,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        async with aclosing(stream.watch(seed)) as events:
            async with asyncio.timeout(3):
                await anext(events)
                first, second = await anext(events), await anext(events)
        assert first.market.last == 98000
        assert second.market.last == 99000


async def test_lagging_ticker_does_not_rewind_fresh_candle_close():
    seed = await DemoClient().market("BTC_USDT", "15m")
    newer = seed.last + 10

    async def handler(websocket):
        await subscribe(websocket, seed)
        candle = candle_message(seed, close=newer)
        candle["ts"] = 3000
        await websocket.send(json.dumps(candle))
        await websocket.send(
            json.dumps(
                {
                    "channel": "push.ticker",
                    "ts": 1000,
                    "data": {
                        "symbol": seed.symbol,
                        "lastPrice": str(seed.last),
                        "fairPrice": "98000",
                        "timestamp": 1000,
                    },
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "channel": "push.ticker",
                    "ts": 4000,
                    "data": {"symbol": seed.symbol, "lastPrice": str(newer + 1), "timestamp": 4000},
                }
            )
        )
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        stream = MarketStream(
            DemoClient().market,
            url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        async with aclosing(stream.watch(seed)) as events:
            async with asyncio.timeout(3):
                await anext(events)
                candle, delayed, recent = (
                    await anext(events),
                    await anext(events),
                    await anext(events),
                )
        assert candle.market.last == newer
        assert delayed.market.last == newer
        assert delayed.market.candles[-1].close == newer
        assert delayed.market.mark == Decimal("98000")
        assert recent.market.last == newer + 1


async def test_same_bucket_updates_reach_chart_with_high_low_and_volume():
    from textual.widgets import Static

    from perpetui.app import TradingApp
    from perpetui.chart import CandleChart

    class StreamingSource(DemoClient):
        streaming = True

        def __init__(self):
            super().__init__()
            self.queue = asyncio.Queue()

        async def stream_market(self, seed):
            from perpetui.models import MarketEvent

            yield MarketEvent("live", seed)
            while True:
                yield MarketEvent("live", apply_message(seed, await self.queue.get()))

    client = StreamingSource()
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        while list(app.workers):
            await app.workers.wait_for_complete()
        await pilot.pause()
        seed = app.market
        picture = app.query_one(CandleChart).render().plain
        bar = seed.candles[-1]
        message = candle_message(
            seed,
            close=bar.close + 25,
            h=str(bar.high + 50),
            l=str(bar.low - 30),
            volume=bar.volume + 500,
        )
        message["data"]["q"] = str(message["data"]["q"])
        await client.queue.put(message)
        await pilot.pause()
        updated = app.query_one(CandleChart).market.candles[-1]
        assert updated.time == bar.time
        assert updated.close == bar.close + 25
        assert updated.high == bar.high + 50
        assert updated.low == bar.low - 30
        assert updated.volume == bar.volume + 500
        assert app.query_one(CandleChart).render().plain != picture
        assert str(updated.close.quantize(Decimal(".01"))) in (
            str(app.query_one("#candle-info", Static).render()).replace(",", "")
        )
