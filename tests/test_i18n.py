import json
from decimal import Decimal
from importlib.resources import files
from string import Formatter

import pytest
from rich.cells import cell_len
from textual.widgets import DataTable, Input, Static

from perpetui.app import TradingApp
from perpetui.chart import build_chart
from perpetui.cli import main
from perpetui.demo import DemoClient
from perpetui.i18n import _, current_language, install_language
from perpetui.search import matching_symbols


@pytest.fixture(autouse=True)
def language_environment(monkeypatch):
    for key in ("PERPETUI_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(key, raising=False)
    install_language("en")
    yield
    install_language("en")


def test_language_precedence_and_english_fallback(monkeypatch):
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    assert install_language() == "ja"
    assert _("Quit") == "終了"
    monkeypatch.setenv("PERPETUI_LANG", "en")
    assert install_language() == "en"
    assert install_language("ja") == "ja"
    monkeypatch.setenv("PERPETUI_LANG", "fr")
    assert install_language() == "en"
    assert _("A new untranslated message") == "A new untranslated message"


def test_translation_placeholders_match_source_catalog():
    catalog = json.loads(files("perpetui").joinpath("locales", "ja.json").read_text())
    formatter = Formatter()

    def fields(value):
        return {field for _, field, _, _ in formatter.parse(value) if field}

    for source, translation in catalog.items():
        assert fields(source) == fields(translation), source
        assert translation


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_japanese_hyperliquid_ui_search_quantity_and_bindings(size):
    install_language("ja")
    app = TradingApp(DemoClient(exchange="hyperliquid"), refresh_seconds=3600)
    async with app.run_test(size=size) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one(DataTable)
        assert app.symbol == "ETH" and "Hyperliquid" in app.title
        assert list(table.columns.values())[2].label.plain == "数量"
        assert app.active_bindings["q"].binding.description == "終了"
        assert app.active_bindings["slash"].binding.description == "銘柄"
        assert "保有ポジション" in app.query_one("#positions-pane").border_title
        assert "現地時刻" in str(app.query_one("#status", Static).render())
        await pilot.press("/", "t", "s", "l", "a", "enter")
        await app.workers.wait_for_complete()
        assert app.symbol == app.market.symbol == "xyz:TSLA"
        assert app.query_one(Input).value == ""
    install_language("en")
    english = TradingApp(DemoClient(), refresh_seconds=3600)
    async with english.run_test() as pilot:
        await english.workers.wait_for_complete()
        assert english.active_bindings["q"].binding.description == "Quit"


@pytest.mark.parametrize("width,height", [(35, 6), (74, 7), (114, 17)])
async def test_japanese_chart_labels_fit_terminal_cells(width, height):
    install_language("ja")
    market = await DemoClient().market("ETH_USDT", "15m")
    output = build_chart(market, Decimal("3472.80"), width, height)
    assert "出来高" in output.plain and "現地時刻" in output.plain
    assert len(output.plain.splitlines()) <= height
    assert max(map(cell_len, output.plain.splitlines())) <= width


def test_search_preserves_hyperliquid_case_and_builder_prefixes():
    assert matching_symbols("xyz", ["xyz:CL", "BTC"]) == ["xyz:CL"]
    assert matching_symbols("pepe", ["kPEPE", "BTC"]) == ["kPEPE"]


def test_localized_cli_help_and_explicit_language(capsys):
    with pytest.raises(SystemExit) as status:
        main(["--lang", "ja", "--help"])
    assert status.value.code == 0
    help_text = capsys.readouterr().out
    assert "表示言語" in help_text and "使用法:" in help_text
    assert "ヘルプを表示して終了します" in help_text
    with pytest.raises(SystemExit):
        main(["--lang=en", "--help"])
    assert "UI language" in capsys.readouterr().out
    assert current_language() == "en"


def test_cli_hyperliquid_demo_does_not_read_accounts(monkeypatch):
    captured = {}

    class FakeApp:
        return_code = 0

        def __init__(self, client, **kwargs):
            captured.update(client=client, **kwargs)

        def run(self):
            pass

    monkeypatch.setattr("perpetui.cli.TradingApp", FakeApp)
    monkeypatch.setattr(
        "perpetui.cli.load_hl_account", lambda *args, **kwargs: pytest.fail("Demo read an account")
    )
    assert main(["--exchange", "hl", "--demo", "--symbol", "xyz:cl", "--lang", "ja"]) == 0
    assert captured["client"].exchange == "Hyperliquid" and captured["symbol"] == "xyz:CL"


def test_cli_rejects_testnet_with_mexc():
    with pytest.raises(SystemExit) as status:
        main(["--exchange", "mexc", "--testnet"])
    assert status.value.code == 2


async def test_provider_errors_are_caught_and_shown_in_the_selected_language():
    from perpetui.errors import ExchangeError

    class FailedHL(DemoClient):
        async def positions(self):
            raise ExchangeError("Hyperliquid connection failed")

        async def market(self, symbol, interval):
            raise ExchangeError("Hyperliquid connection failed")

    install_language("ja")
    app = TradingApp(FailedHL(exchange="hyperliquid"), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.symbol == "BTC"
        assert "更新停止" in str(app.query_one("#status", Static).render())
        assert "ポジション取得失敗" in app._positions_error
        assert "チャート取得失敗" in app._market_error
