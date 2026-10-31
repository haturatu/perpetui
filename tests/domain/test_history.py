from decimal import Decimal

from perpetui.domain.history import CandleStore
from perpetui.domain.models import Candle


def bar(timestamp, closing="100"):
    return Candle(
        timestamp,
        Decimal("99"),
        Decimal("101"),
        Decimal("98"),
        Decimal(closing),
        Decimal("10"),
    )


def test_store_merges_live_updates_deduplicates_and_prepends_history():
    store = CandleStore(limit=5)
    store.reset([bar(3), bar(4)])
    assert store.earliest_time() == 3 and store.latest_time() == 4
    assert not store.merge_live([bar(4)])
    assert store.merge_live([bar(5)])
    assert store.merge_live([bar(4, "101")])
    assert [(item.time, item.close) for item in store.all()][-2:] == [
        (4, Decimal("101")),
        (5, Decimal("100")),
    ]
    assert store.prepend([bar(1), bar(2)])
    assert [item.time for item in store.all()] == [1, 2, 3, 4, 5]
    store.merge_live([bar(6)])
    assert [item.time for item in store.all()] == [2, 3, 4, 5, 6]
    assert store.index_at_or_before(4) == 2
    assert store.nearest(99).time == 6
    assert store.nearest(0).time == 2
