from decimal import Decimal

from perpetui.exchanges.demo import DemoClient


async def test_long_and_short_pnl_and_inverse_contract():
    from dataclasses import replace

    long, short, loss = (await DemoClient().positions()).items
    assert long.pnl == Decimal("54.744")
    assert short.pnl == Decimal("14.399")
    assert loss.pnl == Decimal("-11.44")
    assert replace(long, settlement="BTC").pnl is None
    assert replace(long, mark=None).roe is None
