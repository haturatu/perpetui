import json
from decimal import Decimal
from importlib.resources import files
from string import Formatter

import pytest
from rich.cells import cell_len
from textual.widgets import DataTable, Input, Static

from perpetui.cli import main
from perpetui.exchanges.demo import DemoClient
from perpetui.i18n import LANGUAGES, _, current_language, install_language, normalize_language
from perpetui.ui.app import TradingApp
from perpetui.ui.widgets.chart_plot import build_chart
from perpetui.ui.widgets.search import matching_symbols


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


@pytest.mark.parametrize("language", [code for code in LANGUAGES if code != "en"])
def test_translation_placeholders_match_source_catalog(language):
    catalog = json.loads(
        files("perpetui").joinpath("locales", f"{language}.json").read_text(encoding="utf-8")
    )
    reference = json.loads(
        files("perpetui").joinpath("locales", "ja.json").read_text(encoding="utf-8")
    )
    formatter = Formatter()

    def fields(value):
        return {field for _, field, _, _ in formatter.parse(value) if field}

    assert catalog.keys() == reference.keys()
    for source, translation in catalog.items():
        assert fields(source) == fields(translation), source
        assert translation


@pytest.mark.parametrize(
    "locale,expected",
    [
        ("ru_RU.UTF-8", "ru"),
        ("zh_CN.UTF-8", "zh-cn"),
        ("zh_SG.UTF-8", "zh-cn"),
        ("zh_TW.UTF-8", "zh-tw"),
        ("zh_HK.UTF-8", "zh-tw"),
        ("zh-Hant", "zh-tw"),
        ("zh", "zh-cn"),
        ("ja_JP.UTF-8", "ja"),
        ("fr_FR.UTF-8", "en"),
    ],
)
def test_language_normalization(locale, expected, monkeypatch):
    monkeypatch.setenv("LANG", locale)
    assert install_language() == expected


@pytest.mark.parametrize(
    "language,quit",
    [("ru", "Выход"), ("zh-cn", "退出"), ("zh-tw", "離開")],
)
def test_additional_catalogs_are_installed(language, quit):
    assert normalize_language(language) == language
    assert install_language(language) == language
    assert _("Quit") == quit
    assert install_language("en") == "en"


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


@pytest.mark.parametrize(
    "language,volume,local",
    [
        ("ru", "ОБЪЁМ", "МЕСТНОЕ"),
        ("zh-cn", "成交量", "本地时间"),
        ("zh-tw", "成交量", "本地時間"),
    ],
)
@pytest.mark.parametrize("width,height", [(35, 6), (74, 7), (114, 17)])
async def test_additional_chart_labels_fit_terminal_cells(language, volume, local, width, height):
    install_language(language)
    market = await DemoClient().market("ETH_USDT", "15m")
    output = build_chart(market, Decimal("3472.80"), width, height)
    assert volume in output.plain and local in output.plain
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


@pytest.mark.parametrize(
    "language,usage,help_text",
    [
        ("ru", "использование:", "Показать это сообщение и выйти"),
        ("zh-cn", "用法:", "显示此帮助信息并退出"),
        ("zh-tw", "用法:", "顯示此說明訊息並結束"),
    ],
)
def test_additional_languages_localize_cli_help(capsys, language, usage, help_text):
    with pytest.raises(SystemExit) as status:
        main(["--lang", language, "--help"])
    assert status.value.code == 0
    output = capsys.readouterr().out
    assert usage in output and help_text in output
    install_language("en")


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
        "perpetui.exchanges.factory.load_hl_account",
        lambda *args, **kwargs: pytest.fail("Demo read an account"),
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
        assert "ポジション取得失敗" in app.positions_service.error
        assert "チャート取得失敗" in app.charts[0].state.error
