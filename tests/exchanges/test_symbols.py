import pytest

from perpetui.exchanges.mexc.market import normalize_symbol


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("btc", "BTC_USDT"),
        ("ethusdt", "ETH_USDT"),
        ("SOL/USDT", "SOL_USDT"),
        ("BTC-USD", "BTC_USD"),
        ("1000PEPE", "1000PEPE_USDT"),
    ],
)
def test_symbol_normalization(raw, expected):
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["", "../BTC", "BTC?foo=bar", "ETH_EUR", "BTC_USDT/../x"])
def test_invalid_symbols(raw):
    with pytest.raises(ValueError):
        normalize_symbol(raw)
