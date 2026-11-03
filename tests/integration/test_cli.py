import pytest

from perpetui.cli import main
from perpetui.exchanges.demo import DemoClient
from perpetui.logging_config import configure_logging


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


def test_demo_portfolio_mode_provides_builtin_multi_exchange_accounts(monkeypatch):
    captured = {}

    class FakeApp:
        return_code = 0

        def __init__(self, client, **kwargs):
            captured.update(client=client, **kwargs)

        def run(self):
            pass

    monkeypatch.setattr("perpetui.cli.TradingApp", FakeApp)
    assert main(["--demo=portfolio", "--lang", "en"]) == 0
    config = captured["portfolio_config"]
    assert [account.id for account in config.accounts] == [
        "hl-main",
        "hl-hedge",
        "mexc-trading",
        "lt-main",
    ]
    assert config.default_view == "all"
    assert config.group_by == "exchange"
    assert set(config.profiles) == {"hedge", "all-venues"}
    sources = dict(captured["portfolio_sources"])
    assert {ref.id: client.exchange for ref, client in sources.items()} == {
        "hl-main": "Hyperliquid",
        "hl-hedge": "Hyperliquid",
        "mexc-trading": "MEXC",
        "lt-main": "Lighter",
    }
    assert all(isinstance(client, DemoClient) for client in sources.values())


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_refresh_is_rejected(value):
    with pytest.raises(SystemExit) as error:
        main(["--refresh", value])
    assert error.value.code == 2
