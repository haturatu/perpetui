import asyncio

import pytest
from textual.widgets import DataTable, Input

from perpetui.errors import MEXCError
from perpetui.exchanges.demo import DemoClient
from perpetui.ui.app import TradingApp
from perpetui.ui.widgets.search import Suggestions, matching_symbols


@pytest.mark.parametrize(
    "query,expected",
    [
        ("ZE", ["ZEC_USDT", "ZEN_USDT", "ZETA_USDT", "ZEC_USDC"]),
        (" zec ", ["ZEC_USDT", "ZEC_USDC"]),
        ("zec/usdc", ["ZEC_USDC"]),
        ("zec-usdt", ["ZEC_USDT"]),
        ("zec_usdt", ["ZEC_USDT"]),
        ("zecusdt", ["ZEC_USDT"]),
        ("", []),
        ("qqq", []),
    ],
)
def test_symbol_prefix_matching(query, expected):
    symbols = ["ZEC_USDC", "ZETA_USDT", "ZEN_USDT", "ZEC_USDT", "ZEC_USDT", "BTC_USDT"]
    assert matching_symbols(query, symbols) == expected


def test_exact_base_precedes_longer_prefixes_and_limits_results():
    assert matching_symbols("ZE", ["ZEC_USDT", "ZE_USDT", "ZEN_USDT"], limit=2) == [
        "ZE_USDT",
        "ZEC_USDT",
    ]


def test_partial_symbol_matching_finds_oil_and_prioritizes_prefixes():
    assert matching_symbols("oil", ["USOIL_USDT", "UKOIL_USDT", "OIL_USDC", "SOIL_USDT"]) == [
        "OIL_USDC",
        "SOIL_USDT",
        "UKOIL_USDT",
        "USOIL_USDT",
    ]
    assert matching_symbols("ze", ["AZERO_USDT", "ZEN_USDC"]) == ["ZEN_USDC", "AZERO_USDT"]


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
async def test_suggestions_open_below_input_without_moving_chart_and_choose_first(size):
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        chart_region = app.query_one("#chart-pane").region
        await pilot.press("/", "Z", "E")
        field = app.query_one(Input)
        popup = app.query_one(Suggestions)
        assert field.has_focus
        assert popup.display
        assert popup.region.x == field.region.x
        assert popup.region.y == field.region.bottom
        assert popup.region.bottom < size[1]
        assert app.query_one("#chart-pane").region == chart_region
        assert popup.get_option_at_index(0).id == "ZEC_USDT"
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.symbol == app.market.symbol == "ZEC_USDT"
        assert not popup.display
        assert field.value == ""
        assert app.query_one(DataTable).has_focus


async def test_suggestion_navigation_retains_input_and_does_not_move_position_cursor():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("/", "z", "e", "down")
        popup = app.query_one(Suggestions)
        assert popup.highlighted_option.id == "ZEN_USDT"
        assert app.query_one(Input).has_focus
        assert app.query_one(Input).value == "ze"
        assert app.query_one(DataTable).cursor_row == 0
        await pilot.press("up", "up")
        assert popup.highlighted_option.id == "ZEC_USDC"
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.market.symbol == "ZEC_USDC"


async def test_click_suggestion_selects_chart():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("/", "z", "e")
        await pilot.click(Suggestions, offset=(3, 2))
        await settle(app, pilot)
        assert app.market.symbol == "ZEN_USDT"
        assert not app.query_one(Suggestions).display


async def test_oil_search_selects_actual_exchange_symbol():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("/", "o", "i", "l")
        popup = app.query_one(Suggestions)
        assert popup.option_count == 2
        assert popup.get_option_at_index(0).id == "UKOIL_USDT"
        assert popup.get_option_at_index(1).id == "USOIL_USDT"
        await pilot.press("down", "enter")
        await settle(app, pilot)
        assert app.market.symbol == "USOIL_USDT"


async def test_clear_blur_and_escape_close_suggestions_without_switching_chart():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("/", "z", "e", "ctrl+u")
        popup = app.query_one(Suggestions)
        assert not popup.display
        await pilot.press("z", "e", "tab")
        assert not popup.display
        await pilot.press("/")
        assert popup.display
        await pilot.press("escape")
        assert not popup.display
        assert app.query_one(DataTable).has_focus
        assert app.market.symbol == "ETH_USDT"


async def test_no_match_keeps_manual_symbol_submission():
    app = TradingApp(DemoClient(), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("/", "a", "b", "c")
        popup = app.query_one(Suggestions)
        assert popup.get_option_at_index(0).disabled
        assert str(popup.get_option_at_index(0).prompt) == "No matching symbols"
        await pilot.press("down", "enter")
        await settle(app, pilot)
        assert app.market.symbol == "ABC_USDT"


async def test_delayed_symbol_loading_matches_latest_input():
    class DelayedSymbols(DemoClient):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()

        async def symbols(self):
            await self.release.wait()
            return await super().symbols()

    client = DelayedSymbols()
    app = TradingApp(client, refresh_seconds=3600)
    async with app.run_test() as pilot:
        await pilot.press("/", "z", "e")
        popup = app.query_one(Suggestions)
        assert str(popup.get_option_at_index(0).prompt) == "Loading symbols…"
        await pilot.press("c")
        client.release.set()
        await settle(app, pilot)
        assert popup.option_count == 2
        assert popup.get_option_at_index(0).id == "ZEC_USDT"


async def test_failed_symbol_loading_still_allows_manual_submission():
    class FailedSymbols(DemoClient):
        async def symbols(self):
            raise MEXCError("metadata unavailable")

    app = TradingApp(FailedSymbols(), refresh_seconds=3600)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("/", "z", "e", "c")
        popup = app.query_one(Suggestions)
        assert str(popup.get_option_at_index(0).prompt) == "Suggestions unavailable"
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.market.symbol == "ZEC_USDT"
