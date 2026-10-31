from decimal import Decimal

import pytest

from perpetui.cli import main
from perpetui.exchanges.demo import DemoClient
from perpetui.exchanges.mexc.market import normalize_symbol
from perpetui.logging_config import configure_logging


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


async def test_long_and_short_pnl_and_inverse_contract():
    from dataclasses import replace

    long, short, loss = (await DemoClient().positions()).items
    assert long.pnl == Decimal("54.744")
    assert short.pnl == Decimal("14.399")
    assert loss.pnl == Decimal("-11.44")
    assert replace(long, settlement="BTC").pnl is None
    assert replace(long, mark=None).roe is None


def test_log_levels_go_to_correct_stream(capsys):
    logger = configure_logging(debug=True)
    logger.debug("debug output")
    logger.info("info output")
    logger.warning("warning output")
    logger.error("error output")
    captured = capsys.readouterr()
    assert "DEBUG debug output" in captured.out
    assert "INFO info output" in captured.out
    assert "WARNING" not in captured.out
    assert "WARNING warning output" in captured.err
    assert "ERROR error output" in captured.err
    logger.handlers.clear()


def test_fatal_configuration_error_exits_one(monkeypatch, capsys):
    monkeypatch.setenv("MEXC_API_KEY", "key")
    monkeypatch.delenv("MEXC_API_SECRET", raising=False)
    assert main(["--exchange", "mexc"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR" in captured.err
    configure_logging().handlers.clear()


def test_tui_fatal_exit_code_reaches_the_shell(monkeypatch):
    class FatalApp:
        return_code = 1

        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            pass

    monkeypatch.setattr("perpetui.cli.TradingApp", FatalApp)
    assert main(["--demo"]) == 1
    configure_logging().handlers.clear()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_refresh_is_rejected(value):
    with pytest.raises(SystemExit) as error:
        main(["--refresh", value])
    assert error.value.code == 2
