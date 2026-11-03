"""Per-pane exchange switching must respect each venue's interval set."""

from textual.widgets import Select

from perpetui.config import PortfolioConfig
from perpetui.exchanges.demo import DemoClient
from perpetui.services.portfolio import AccountRef
from perpetui.ui.app import TradingApp
from perpetui.ui.widgets.chart_panel import ExtraChart


async def settle(app, pilot):
    while list(app.workers):
        await app.workers.wait_for_complete()
    await pilot.pause()


async def test_exchange_switch_clamps_interval_and_timeframe_follows_active_pane():
    app = TradingApp(
        DemoClient(exchange="hyperliquid"),
        interval="1w",
        portfolio_sources=[
            (AccountRef("hl", "hyperliquid", label="main"), DemoClient(exchange="hyperliquid")),
            (AccountRef("lt", "lighter", label="lighter"), DemoClient(exchange="lighter")),
        ],
        portfolio_config=PortfolioConfig(default_view="all"),
        client_factory=lambda exchange: DemoClient(exchange=exchange),
        refresh_seconds=3600,
    )
    async with app.run_test(size=(150, 46)) as pilot:
        await settle(app, pilot)
        app.action_chart_count(2)
        await settle(app, pilot)
        second = app.query_one("#chart-pane-2", ExtraChart)
        second.focus()
        await settle(app, pilot)

        # Lighter has no 1w interval; the pane must fall back instead of crashing.
        app.query_one("#exchange", Select).value = "lighter"
        await settle(app, pilot)
        assert second.session.hub.client.exchange.lower() == "lighter"
        assert second.session.state.interval == "15m"
        assert app.query_one("#timeframe", Select).value == "15m"

        app.action_interval(1)
        await settle(app, pilot)
        assert second.session.state.interval == "30m"

        app.query_one("#chart-pane").focus()
        await settle(app, pilot)
        assert app.query_one("#timeframe", Select).value == "1w"

        second.focus()
        await settle(app, pilot)
        assert app.query_one("#timeframe", Select).value == "30m"
