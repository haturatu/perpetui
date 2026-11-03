"""End-to-end account filtering, exposure view and per-pane exchange routing."""

from textual.widgets import DataTable, Select

from perpetui.config import PortfolioConfig
from perpetui.exchanges.demo import DemoClient
from perpetui.services.portfolio import AccountRef
from perpetui.ui.app import TradingApp
from perpetui.ui.widgets.chart_panel import ExtraChart


async def settle(app, pilot):
    while list(app.workers):
        await app.workers.wait_for_complete()
    await pilot.pause()


async def test_multi_account_filters_risk_and_independent_chart_venues():
    app = TradingApp(
        DemoClient(exchange="hyperliquid"),
        portfolio_sources=[
            (AccountRef("hl", "hyperliquid", label="main"), DemoClient(exchange="hyperliquid")),
            (AccountRef("mexc", "mexc", label="trading"), DemoClient(exchange="mexc")),
        ],
        portfolio_config=PortfolioConfig(profiles={"hedge": ("hl", "mexc")}, default_view="all"),
        client_factory=lambda exchange: DemoClient(exchange=exchange),
        refresh_seconds=3600,
    )
    async with app.run_test(size=(140, 44)) as pilot:
        await settle(app, pilot)
        assert len(app.positions) == 6
        assert app.query_one("#positions", DataTable).row_count == 6

        app.query_one("#position-scope", Select).value = "account:mexc"
        await settle(app, pilot)
        assert len(app.positions) == 3
        assert all(item.account.exchange == "mexc" for item in app.positions)
        status = str(app.query_one("#status").render())
        assert "3 positions" in status
        assert "6 positions" not in status

        app.query_one("#position-group", Select).value = "exposure"
        await settle(app, pilot)
        assert not app.query_one("#positions", DataTable).display
        assert app.query_one("#exposures", DataTable).row_count == 3

        app.query_one("#position-group", Select).value = "none"
        app.query_one("#exchange", Select).value = "mexc"
        await settle(app, pilot)
        assert app.charts[0].hub.client.exchange.lower() == "mexc"

        app.action_chart_count(2)
        await settle(app, pilot)
        second = app.query_one("#chart-pane-2", ExtraChart)
        second.focus()
        await settle(app, pilot)
        app.query_one("#exchange", Select).value = "hyperliquid"
        await settle(app, pilot)
        assert app.charts[0].hub.client.exchange.lower() == "mexc"
        assert second.session.hub.client.exchange.lower() == "hyperliquid"
        assert len(app.positions) == 3


async def test_configured_default_view_and_group_are_applied_on_startup():
    app = TradingApp(
        DemoClient(exchange="hyperliquid"),
        portfolio_sources=[
            (AccountRef("hl", "hyperliquid", label="main"), DemoClient(exchange="hyperliquid")),
            (AccountRef("mexc", "mexc", label="trading"), DemoClient(exchange="mexc")),
        ],
        portfolio_config=PortfolioConfig(
            profiles={"hedge": ("hl", "mexc")}, default_view="account:hl", group_by="symbol"
        ),
        client_factory=lambda exchange: DemoClient(exchange=exchange),
        refresh_seconds=3600,
    )
    async with app.run_test(size=(140, 44)) as pilot:
        await settle(app, pilot)
        assert app._portfolio_scope == "account:hl"
        assert app._portfolio_group == "symbol"
        assert app.query_one("#position-scope", Select).value == "account:hl"
        assert app.query_one("#position-group", Select).value == "symbol"
        assert len(app.positions) == 3
        assert all(item.account.id == "hl" for item in app.positions)
