from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Input, Select, Static

from perpetui.config import PortfolioConfig
from perpetui.domain.intervals import INTERVAL_SECONDS
from perpetui.domain.models import Market, Position, Positions
from perpetui.errors import CredentialsError, ExchangeError
from perpetui.exchanges.base import DataSource
from perpetui.i18n import _
from perpetui.services.chart_session import ChartSession
from perpetui.services.history import HistoryService
from perpetui.services.market_feed import MarketFeedHub
from perpetui.services.portfolio import AccountRef, PortfolioPosition, PortfolioService
from perpetui.services.positions import PositionsService
from perpetui.ui.formatting import compact, price
from perpetui.ui.layout import LAYOUT_MODES
from perpetui.ui.state import WorkspaceState
from perpetui.ui.theme import ACCENT, DOWN, MUTED, UP
from perpetui.ui.widgets.adaptive_grid import AdaptiveGrid
from perpetui.ui.widgets.chart import CandleChart
from perpetui.ui.widgets.chart_panel import ChartFrame, ChartPanel, ExtraChart
from perpetui.ui.widgets.positions import PositionsPane
from perpetui.ui.widgets.search import SymbolSearch
from perpetui.ui.workspace import ChartWorkspace

logger = logging.getLogger("perpetui")


class TradingApp(ChartWorkspace, App[None]):
    TITLE = "perpetui"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", show=False, priority=True),
        Binding("r", "refresh", "Refresh"),
        Binding("slash", "search", "Symbol"),
        Binding("e", "exchange", "Exchange"),
        *[
            Binding(
                f"ctrl+{count}",
                f"chart_count({count})",
                "Charts",
                show=count == 1,
                key_display="Ctrl+1–9",
                priority=True,
            )
            for count in range(1, 10)
        ],
        Binding("left_square_bracket", "interval(-1)", "Timeframe −"),
        Binding("right_square_bracket", "interval(1)", "Timeframe +"),
        Binding(
            "shift+left_square_bracket,left_curly_bracket",
            "interval_all(-1)",
            "All timeframes −",
            show=False,
        ),
        Binding(
            "shift+right_square_bracket,right_curly_bracket",
            "interval_all(1)",
            "All timeframes +",
            show=False,
        ),
        Binding("plus,equals", "price_zoom(1)", "Price +"),
        Binding("minus", "price_zoom(-1)", "Price −"),
        Binding("0", "price_zoom(0)", "Reset"),
        Binding("v", "candle_detail", "Style"),
        *[
            Binding(str(index), f"select_chart({index})", "Select chart", show=False)
            for index in range(1, 10)
        ],
        Binding("a", "toggle_timeframe_sync", "All TF", show=False),
        Binding("s", "toggle_cursor_sync", "Sync time", show=False),
        Binding("z", "maximize", "Maximize", show=False),
        Binding("p", "toggle_positions", "Positions", show=False),
        Binding("shift+p", "auto_positions", "Auto positions", show=False),
        Binding("l", "layout", "Layout", show=False),
        Binding("ctrl+up", "time_zoom(1)", "Time zoom out", show=False),
        Binding("ctrl+down", "time_zoom(-1)", "Time zoom in", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("ctrl+p", "command_palette", "Commands", show=False),
        Binding("escape", "positions", show=False),
        Binding("j", "next_position(1)", show=False),
        Binding("k", "next_position(-1)", show=False),
    ]

    def __init__(
        self,
        client: DataSource,
        *,
        symbol: str | None = None,
        interval: str = "15m",
        refresh_seconds: float = 5,
        client_factory: Callable[[str], DataSource] | None = None,
        portfolio_sources: list[tuple[AccountRef, DataSource]] | None = None,
        portfolio_config: PortfolioConfig | None = None,
        initial_network: str = "mainnet",
    ) -> None:
        super().__init__()
        for key, bindings in self._bindings.key_to_bindings.items():
            self._bindings.key_to_bindings[key] = [
                replace(binding, description=_(binding.description)) for binding in bindings
            ]
        self.title = f"{client.exchange} Terminal"
        self.client = client
        self._client_factory = client_factory
        self._generation = 0
        self._switching = False
        self._closing = False
        self.workspace = WorkspaceState()
        self.workspace.chart_count = self.workspace.desired_chart_count = 1
        self._layout_worker = None
        self._layout_signature: tuple[tuple[int, ...], tuple[ChartFrame, ...]] | None = None
        self._all_interval_timer = None
        self._pending_interval: str | None = None
        self._syncing_cursor = False
        self._initial_interval = interval
        self.refresh_seconds = refresh_seconds
        self._reset_data(symbol)
        self._initial_network = initial_network
        initial_key = (
            client.exchange.lower()
            if initial_network == "mainnet"
            else f"{client.exchange.lower()}:{initial_network}"
        )
        self._market_hubs = {initial_key: self.feed_hub}
        self._market_histories = {initial_key: self.history_service}
        self._chart_clients = {initial_key: client}
        self.portfolio = (
            PortfolioService(
                portfolio_sources,
                profiles=portfolio_config.profiles if portfolio_config else {},
                fx_rates=portfolio_config.fx_rates if portfolio_config else {},
            )
            if portfolio_sources is not None
            else None
        )
        self._portfolio_scope = portfolio_config.default_view if portfolio_config else "all"
        self._portfolio_group = portfolio_config.group_by if portfolio_config else "none"
        if self.portfolio is not None:
            self.portfolio.on_change = self._portfolio_changed

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "select_chart" and isinstance(getattr(self, "focused", None), (Input, Select)):
            return False
        return True

    def _intervals(self) -> tuple[str, ...]:
        client = self.client
        if getattr(self, "portfolio", None) is not None and self.is_mounted:
            client = self._active_frame().session.hub.client
        return tuple(getattr(client, "intervals", tuple(INTERVAL_SECONDS)))

    @property
    def symbol(self) -> str:
        return self.charts[0].state.symbol

    @symbol.setter
    def symbol(self, value: str) -> None:
        self.charts[0].configure(symbol=value)

    @property
    def interval(self) -> str:
        return self.charts[0].state.interval

    @interval.setter
    def interval(self, value: str) -> None:
        self.charts[0].configure(interval=value)

    @property
    def market(self) -> Market | None:
        return self.charts[0].state.market

    @property
    def positions(self) -> list[Position | PortfolioPosition]:
        if self.portfolio is not None:
            return self.portfolio.view(self._portfolio_scope, self._portfolio_group)
        return self.positions_service.snapshot.items

    @property
    def _active_extra(self) -> ExtraChart | None:
        if self.workspace.active_chart_id == 1:
            return None
        return self.query_one(f"#chart-pane-{self.workspace.active_chart_id}", ExtraChart)

    @_active_extra.setter
    def _active_extra(self, panel: ExtraChart | None) -> None:
        self.workspace.active_chart_id = panel.index if panel else 1

    def _reset_data(self, symbol: str | None = None) -> None:
        if self._all_interval_timer is not None:
            self._all_interval_timer.stop()
            self._all_interval_timer = None
        self._pending_interval = None
        interval = self.interval if getattr(self, "charts", None) else self._initial_interval
        supported = self._intervals()
        if interval not in supported:
            interval = "15m" if "15m" in supported else supported[0]
        self.feed_hub = MarketFeedHub(self.client)
        self.history_service = HistoryService(self.client)
        self.positions_service = PositionsService(self.client)
        self.positions_service.on_change = self._positions_changed
        self.charts = [
            ChartSession(self.feed_hub, 1, symbol or self.client.default_symbol, interval)
        ]
        self._manual_symbol = symbol is not None
        self._initial_selection = False
        self._selected_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(f"◈  {self.client.exchange.upper()}  /  TERMINAL", id="brand")
            yield Select(
                [("Hyperliquid", "hyperliquid"), ("Lighter", "lighter"), ("MEXC", "mexc")],
                value=self.client.exchange.lower(),
                allow_blank=False,
                compact=True,
                id="exchange",
                tooltip=_("Exchange"),
                disabled=self._client_factory is None,
            )
            yield Select(
                [(f"×{n}", n) for n in range(1, 10)],
                value=1,
                allow_blank=False,
                compact=True,
                id="chart-count",
                tooltip=_("Charts"),
            )
            yield Select(
                [(_(mode.title()), mode) for mode in LAYOUT_MODES],
                value=self.workspace.layout_mode,
                allow_blank=False,
                compact=True,
                id="chart-layout",
                tooltip=_("Chart layout"),
            )
            yield Static(Text(self.client.label), id="connection")
        yield Static(_("Loading market…"), id="market-summary")
        with Horizontal(id="toolbar"):
            yield SymbolSearch()
            yield Select(
                [(label, label) for label in self._intervals()],
                value=self.interval,
                allow_blank=False,
                id="timeframe",
            )
            yield Static("", id="candle-info")
        with AdaptiveGrid(id="chart-grid"):
            yield ChartPanel(self.charts[0], autoload=False)
        yield PositionsPane(id="positions-pane")
        yield Static(_("Connecting…"), id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#positions-pane").border_title = " " + _("OPEN POSITIONS") + " "
        self._setup_table()
        self.watch(
            self.query_one(DataTable), "show_horizontal_scrollbar", self._update_positions_layout
        )
        self.action_positions()
        self._title_chart()
        self._load_initial_data()
        self.set_interval(self.refresh_seconds, self.refresh_data)

    def _update_timeframe(self) -> None:
        selector = self.query_one("#timeframe", Select)
        with selector.prevent(Select.Changed):
            selector.set_options([(label, label) for label in self._intervals()])
            selector.value = self.interval

    def _setup_table(self) -> None:
        pane = self.query_one(PositionsPane)
        pane.setup(self.client, portfolio=self.portfolio is not None)
        if self.portfolio is not None:
            pane.configure_portfolio(
                self.portfolio.options(), self._portfolio_scope, self._portfolio_group
            )
        self._update_positions_layout()

    def _load_initial_data(self) -> None:
        if self.portfolio is not None:
            self.load_market()
            self.load_symbols()
            self.load_portfolio()
            return
        # Explicit symbols and public mode can load without waiting for private requests.
        if self._manual_symbol or not self.client.authenticated:
            self.load_market()
        if self.client.authenticated:
            self.load_positions()
        else:
            self._initial_selection = True
            self._status()
        self.load_symbols()

    def _view_ready(self) -> bool:
        return not self._closing and self.is_running and bool(self.query("#chart"))

    @work(exclusive=True, group="symbols")
    async def load_symbols(self) -> None:
        if not self._view_ready():
            return
        client = (
            self._active_frame().session.hub.client if self.portfolio is not None else self.client
        )
        generation = self._generation
        search = self.query_one(SymbolSearch)
        try:
            symbols = await client.symbols()
            if (
                generation == self._generation
                and self._view_ready()
                and (self.portfolio is None or client is self._active_frame().session.hub.client)
            ):
                search.set_symbols(symbols)
        except ExchangeError as exc:
            if generation != self._generation:
                return
            logger.warning("Symbol suggestions unavailable (%s)", type(exc).__name__)
            search.set_symbols([], unavailable=True)

    def on_resize(self) -> None:
        self.screen.set_class(self.size.height < 32, "compact")
        if self.query("#positions-pane"):
            self._update_positions_layout()

    async def on_unmount(self) -> None:
        self._closing = True
        self._generation += 1
        workers = [
            worker
            for group in ("exchange", "layout")
            for worker in self.workers.cancel_group(self, group)
        ]
        await asyncio.gather(*(worker.wait() for worker in workers), return_exceptions=True)
        await asyncio.gather(*(panel.stop() for panel in self.query(ChartPanel)))
        await self.positions_service.close()
        await asyncio.gather(
            *(service.close() for service in self._market_histories.values()),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(hub.close() for hub in self._market_hubs.values()),
            return_exceptions=True,
        )
        if self.portfolio is not None:
            await self.portfolio.close()
        await asyncio.gather(
            *(client.close() for client in self._chart_clients.values()),
            return_exceptions=True,
        )

    def _entry_for(self, symbol: str, exchange: str | None = None) -> Decimal | None:
        if exchange is None:
            exchange = (
                self._active_frame().session.hub.client.exchange
                if self.portfolio is not None and self.is_mounted
                else self.client.exchange.lower()
            )
        matches = [
            p
            for p in self.positions
            if p.symbol == symbol
            and (self.portfolio is None or p.account.exchange == exchange.lower())
        ]
        selected = next((p for p in matches if p.id == self._selected_id), None)
        return (selected or matches[0]).entry if matches else None

    def _market_hub_for(self, exchange: str, account_id: str | None = None) -> MarketFeedHub:
        exchange = exchange.lower()
        account = (
            self.portfolio.accounts.get(account_id)
            if self.portfolio is not None and account_id
            else None
        )
        network = account.network if account is not None else "mainnet"
        key = exchange if network == "mainnet" else f"{exchange}:{network}"
        hub = self._market_hubs.get(key)
        if hub is None:
            if account is not None and network != "mainnet":
                # Reuse a read-only monitored account for the selected testnet.
                client = self.portfolio.clients[account_id]
            else:
                if self._client_factory is None:
                    raise ValueError("Chart client factory is not configured")
                client = self._client_factory(exchange)
                self._chart_clients[key] = client
            hub = self._market_hubs[key] = MarketFeedHub(client)
            self._market_histories[key] = HistoryService(client)
        return hub

    def _change_chart_exchange(
        self,
        pane: ChartPanel,
        exchange: str,
        symbol: str | None = None,
        account_id: str | None = None,
    ) -> None:
        hub = self._market_hub_for(exchange, account_id)
        if pane.session.hub is hub:
            if symbol is not None and symbol != pane.symbol:
                pane.change(symbol=hub.client.normalize_symbol(symbol))
            return
        old = pane.session
        interval = (
            pane.interval
            if pane.interval in hub.client.intervals
            else ("15m" if "15m" in hub.client.intervals else hub.client.intervals[0])
        )
        new_session = ChartSession(
            hub,
            pane.index,
            hub.client.normalize_symbol(symbol) if symbol else hub.client.default_symbol,
            interval,
        )
        old.on_change = lambda: None
        pane.session = new_session
        new_session.on_change = pane._session_changed
        self.charts[pane.index - 1] = new_session
        asyncio.create_task(old.stop())
        pane.set_entry(None)
        pane._session_changed()
        pane.load_market()
        if pane is self._active_frame():
            self._update_timeframe()
            self.query_one(SymbolSearch).reset()
            self.load_symbols()
        self._status()

    def action_exchange(self) -> None:
        selector = self.query_one("#exchange", Select)
        if not selector.disabled:
            selector.focus()
            selector.expanded = True

    @on(Select.Changed, "#exchange")
    def exchange_changed(self, event: Select.Changed) -> None:
        if self.portfolio is not None and not self._closing and isinstance(event.value, str):
            pane = self._active_frame()
            if event.value != pane.session.hub.client.exchange.lower():
                try:
                    self._change_chart_exchange(pane, event.value)
                except (CredentialsError, ExchangeError, ValueError, OSError) as exc:
                    self.notify(
                        _("Cannot switch exchange: {error}", error=str(exc)), severity="error"
                    )
                    selector = self.query_one("#exchange", Select)
                    with selector.prevent(Select.Changed):
                        selector.value = pane.session.hub.client.exchange.lower()
            return
        if (
            self._client_factory is not None
            and not self._switching
            and not self._closing
            and isinstance(event.value, str)
            and event.value != self.client.exchange.lower()
        ):
            self._switching = True
            self.query_one("#exchange").disabled = True
            self.query_one("#chart-count").disabled = True
            self.query_one("#toolbar").disabled = True
            self.switch_exchange(event.value)

    @work(group="exchange")
    async def switch_exchange(self, exchange: str) -> None:
        candidate, retired = None, None
        try:
            try:
                if self._client_factory is None:
                    raise ValueError("Client factory is not configured")
                candidate = self._client_factory(exchange)
            except (CredentialsError, ExchangeError, ValueError, OSError) as exc:
                logger.warning("Cannot switch exchange (%s)", type(exc).__name__)
                self.notify(
                    _("Cannot switch exchange: {error}", error=_(str(exc))), severity="error"
                )
                selector = self.query_one("#exchange", Select)
                with selector.prevent(Select.Changed):
                    selector.value = self.client.exchange.lower()
                return
            self.query_one("#connection", Static).update(_("Switching exchange…"))
            self._generation += 1
            if self._layout_worker is not None:
                await asyncio.gather(self._layout_worker.wait(), return_exceptions=True)
            workers = [
                worker
                for group in ("market", "positions", "symbols")
                for worker in self.workers.cancel_group(self, group)
            ]
            await asyncio.gather(*(worker.wait() for worker in workers), return_exceptions=True)
            await asyncio.gather(*(panel.stop() for panel in self.query(ChartPanel)))
            await self.positions_service.close()
            await self.history_service.close()
            await self.feed_hub.close()
            settings = {
                panel.index: (
                    panel.interval,
                    panel.query_one(CandleChart).detail,
                    panel.query_one(CandleChart).price_zoom,
                )
                for panel in self.query(ExtraChart)
            }
            await self._remove_extra_charts()
            retired, self.client = self.client, candidate
            candidate = None
            self._reset_data()
            self._market_hubs = {self.client.exchange.lower(): self.feed_hub}
            self._market_histories = {self.client.exchange.lower(): self.history_service}
            self._chart_clients = {self.client.exchange.lower(): self.client}
            primary = self.query_one("#chart-pane", ChartPanel)
            primary.session = self.charts[0]
            primary.session.on_change = primary._session_changed
            primary.entry = None
            primary.manual_symbol = False
            primary.autoload = False
            self._update_timeframe()
            self.title = f"{self.client.exchange} Terminal"
            self.query_one("#brand", Static).update(
                f"◈  {self.client.exchange.upper()}  /  TERMINAL"
            )
            self.query_one("#connection", Static).update(Text(self.client.label))
            self.query_one(SymbolSearch).reset()
            self.query_one("#market-summary", Static).update(_("Loading market…"))
            self.query_one("#candle-info", Static).update("")
            self.query_one("#positions-summary", Static).update("")
            self._setup_table()
            self.query_one(DataTable).display = False
            self.query_one("#empty", Static).update(_("Loading open positions…"))
            self.query_one("#empty").display = True
            chart = self.query_one("#chart", CandleChart)
            chart.reset_market()
            await self._reconcile_charts(self.workspace.chart_count, settings)
            self._apply_maximized_layout()
            self._title_chart()
            self._status()
            await retired.close()
            retired = None
            self._load_initial_data()
        finally:
            if candidate is not None:
                await candidate.close()
            if retired is not None:
                await retired.close()
            self._switching = False
            if not self._closing and self.query("#exchange"):
                self.query_one("#exchange").disabled = self._client_factory is None
                self.query_one("#chart-count").disabled = False
                self.query_one("#toolbar").disabled = False
                self.action_positions()

    def _entry(self) -> Decimal | None:
        return self._entry_for(self.symbol, self.charts[0].hub.client.exchange.lower())

    def refresh_data(self) -> None:
        if self._switching or self._closing:
            return
        for panel in self.query(ExtraChart):
            panel.refresh_data()
        if self.portfolio is not None:
            if any(not s.busy and s.connection != "live" for s in self.portfolio.services.values()):
                self.load_portfolio()
        elif (
            self.client.authenticated
            and not self.positions_service.busy
            and self.positions_service.connection != "live"
        ):
            self.load_positions()
        if (
            (self._initial_selection or self.market is not None)
            and not self.charts[0].busy
            and self.charts[0].state.connection != "live"
        ):
            self.load_market(restart_stream=False)

    @work(exclusive=True, group="positions")
    async def load_positions(self, restart_stream: bool = False) -> None:
        if not self._view_ready():
            return
        service = self.positions_service
        await service.load(restart_stream=restart_stream)
        if service is not self.positions_service or not self._view_ready():
            return
        if service.error and not self._initial_selection:
            self.load_market()
            self._start_extra_defaults()
        self._status()

    @work(exclusive=True, group="portfolio")
    async def load_portfolio(self, restart_stream: bool = False) -> None:
        if not self._view_ready() or self.portfolio is None:
            return
        await self.portfolio.refresh(restart_stream=restart_stream)
        self._status()

    def _portfolio_changed(self) -> None:
        if not self._view_ready() or self.portfolio is None:
            return
        if not self._initial_selection and self.portfolio.ready:
            if not self._manual_symbol:
                matching = next(
                    (
                        p
                        for p in self.positions
                        if p.account.exchange == self.client.exchange.lower()
                    ),
                    None,
                )
                self.symbol = (
                    matching.symbol if matching is not None else self.client.default_symbol
                )
                self.load_market()
            self._initial_selection = True
            self._start_extra_defaults()
        self._render_positions()
        for panel in self.query(ChartPanel):
            panel.set_entry(
                self._entry_for(panel.symbol, panel.session.hub.client.exchange.lower())
            )
        self._status()

    @on(Select.Changed, "#position-scope")
    def position_scope_changed(self, event: Select.Changed) -> None:
        if self.portfolio is not None and isinstance(event.value, str):
            self._portfolio_scope = event.value
            self._render_positions()

    @on(Select.Changed, "#position-group")
    def position_group_changed(self, event: Select.Changed) -> None:
        if self.portfolio is not None and event.value in {"none", "exchange", "symbol", "exposure"}:
            self._portfolio_group = str(event.value)
            self._render_positions()

    def _set_positions(self, snapshot: Positions) -> None:
        if not self._view_ready():
            return
        self.positions_service.accept(snapshot)

    def _positions_changed(self) -> None:
        if not self._view_ready():
            return
        if self.positions_service.error:
            self._render_positions()
            self._status()
            return
        if not self.positions_service.revision:
            self._status()
            return
        if not self._initial_selection:
            if not self._manual_symbol:
                self.symbol = (
                    self.positions[0].symbol if self.positions else self.client.default_symbol
                )
            self._initial_selection = True
            if not self._manual_symbol and self.client.authenticated:
                self.load_market()
            self._start_extra_defaults()
        self._render_positions()
        for panel in self.query(ChartPanel):
            panel.set_entry(self._entry_for(panel.symbol))
        self._status()

    def _start_extra_defaults(self) -> None:
        for panel in self.query(ExtraChart):
            if panel.manual_symbol:
                continue
            panel.set_entry(self._entry_for(self._default_chart_symbol(panel.index)))
            panel.change(symbol=self._default_chart_symbol(panel.index))

    def load_market(self, restart_stream: bool = True):
        pane = self.query_one("#chart-pane", ChartPanel)
        pane.autoload = True
        return pane.load_market(restart_stream=restart_stream)

    def _render_market(self, market: Market, *, force: bool = False) -> None:
        if self._active_extra is not None and not force:
            return
        text = Text()
        text.append(market.symbol.replace("_", " / "), style="bold #e4edf8")
        text.append("  " + price(market.last), style=f"bold {UP if market.change >= 0 else DOWN}")
        text.append(f"  {market.change:+.2f}%", style=UP if market.change >= 0 else DOWN)
        text.append("  24h\n", style=MUTED)
        text.append(
            _(
                "Mark {mark}   High {high}   Low {low}   Turnover {amount}",
                mark=price(market.mark),
                high=price(market.high),
                low=price(market.low),
                amount=compact(market.amount),
            ),
            style=MUTED,
        )
        self.query_one("#market-summary", Static).update(text)
        self._update_candle_info()

    def _update_candle_info(self, chart: CandleChart | None = None) -> None:
        chart = chart or self._active_chart()
        bar = chart.selected_candle()
        if bar is None:
            self.query_one("#candle-info", Static).update("")
            return
        stamp = datetime.fromtimestamp(bar.time).astimezone().strftime("%m/%d %H:%M")
        text = Text(f"{stamp}  O {price(bar.open)}  H {price(bar.high)}  ")
        text.append(f"L {price(bar.low)}  C {price(bar.close)}  ")
        text.append(_("V {volume}", volume=compact(bar.volume)))
        text.append("  " + chart.mode_label(), style=ACCENT if chart.cursor_offset() else MUTED)
        self.query_one("#candle-info", Static).update(text)

    def _render_positions(self) -> None:
        error = (
            "; ".join(f"{key}: {message}" for key, message in self.portfolio.errors.items())
            if self.portfolio is not None
            else self.positions_service.error
        )
        self._selected_id = self.query_one(PositionsPane).render_positions(
            self.positions,
            self._selected_id,
            self.client,
            error,
            portfolio=self.portfolio,
        )
        self._update_positions_layout()

    def _update_positions_layout(self) -> None:
        self.query_one(PositionsPane).update_layout(
            self.client,
            (
                len(self.portfolio.exposures(self.positions))
                if self.portfolio is not None and self._portfolio_group == "exposure"
                else len(self.positions)
            ),
            (
                "; ".join(f"{key}: {message}" for key, message in self.portfolio.errors.items())
                if self.portfolio is not None
                else self.positions_service.error
            ),
            self._initial_selection,
            self.workspace.positions_override,
            self.size.height,
            self._active_frame().focus,
            portfolio_enabled=self.portfolio is not None,
        )

    @property
    def positions_warning(self) -> str:
        if self.portfolio is not None:
            return " · ".join(
                f"{identifier}: {warning}"
                for identifier, service in self.portfolio.services.items()
                for warning in service.snapshot.warnings
            )
        return " · ".join(self.positions_service.snapshot.warnings)

    def _status(self) -> None:
        if not self._view_ready() or not self.query("#status"):
            return
        status = self.query_one("#status", Static)
        self.query_one("#positions-pane").border_title = f" {_('OPEN POSITIONS')} "
        market_error = (
            self._active_extra.error if self._active_extra else self.charts[0].state.error
        )
        errors = (
            [f"{key}: {value}" for key, value in self.portfolio.errors.items()]
            if self.portfolio is not None
            else [self.positions_service.error] if self.positions_service.error else []
        )
        errors.extend([market_error] if market_error else [])
        status.set_class(bool(errors), "error")
        if errors:
            message = _("STALE") + " · " + " · ".join(errors)
        else:
            message = _(
                "Chart {chart_time}  ·  Positions {positions_time}  ·  Local time"
                if self.client.authenticated or self.portfolio is not None
                else "Chart {chart_time}  ·  Local time",
                chart_time=(
                    self._active_extra.updated
                    if self._active_extra
                    else self.charts[0].state.updated
                )
                or "…",
                positions_time=(
                    max((s.updated for s in self.portfolio.services.values()), default="…")
                    if self.portfolio is not None
                    else self.positions_service.updated or "…"
                ),
            )
        if self.positions_warning:
            message += " · " + self.positions_warning
        if self.client.authenticated or self.portfolio is not None:
            message += " · " + _("{count} positions", count=len(self.positions))
        flags = []
        if self.workspace.all_timeframes:
            flags.append(_("ALL TF: ON"))
        if self.workspace.cursor_sync:
            flags.append(_("CURSOR SYNC: ON"))
        if self.workspace.maximized:
            flags.append(_("MAXIMIZED"))
        if flags:
            message += " · " + " · ".join(flags)
        status.update(Text(message))
        status.tooltip = message

    @on(DataTable.RowHighlighted, "#positions")
    @on(DataTable.RowSelected, "#positions")
    def position_highlighted(self, event: DataTable.RowHighlighted | DataTable.RowSelected) -> None:
        if self._switching:
            return
        position = next((p for p in self.positions if p.id == event.row_key.value), None)
        if position:
            self._selected_id = position.id
            self._manual_symbol = True
            pane = self._active_frame()
            pane.manual_symbol = True
            pane.set_entry(position.entry)
            if self.portfolio is not None:
                try:
                    self._change_chart_exchange(
                        pane,
                        position.account.exchange,
                        position.symbol,
                        account_id=position.account.id,
                    )
                    pane.set_entry(position.entry)
                    self.query_one("#exchange", Select).value = position.account.exchange
                except (CredentialsError, ExchangeError, ValueError, OSError) as exc:
                    self.notify(str(exc), severity="error")
            elif pane.symbol != position.symbol:
                pane.change(symbol=position.symbol)

    @on(SymbolSearch.Chosen)
    def submit_symbol(self, event: SymbolSearch.Chosen) -> None:
        if self._switching:
            return
        try:
            active_client = self._active_frame().session.hub.client
            symbol = active_client.normalize_symbol(event.value)
        except ValueError as exc:
            self.notify(_(str(exc)), severity="warning")
            return
        pane = self._active_frame()
        pane.manual_symbol = True
        if pane.index == 1:
            self._manual_symbol = True
        pane.set_entry(self._entry_for(symbol))
        pane.change(symbol=symbol)
        self.query_one(Input).value = ""
        self.action_positions()

    @on(SymbolSearch.Cancelled)
    def cancel_search(self) -> None:
        self.action_positions()

    @on(Select.Changed, "#timeframe")
    def timeframe_changed(self, event: Select.Changed) -> None:
        if self._switching or event.value not in self._intervals():
            return
        if self.workspace.all_timeframes:
            self._pending_interval = None
            self.set_interval_all(event.value)
            return
        pane = self._active_frame()
        if isinstance(event.value, str) and event.value != pane.interval:
            pane.change(interval=event.value)

    def action_search(self) -> None:
        if self._switching:
            return
        self.query_one(Input).focus()

    def action_positions(self) -> None:
        if (self.portfolio is not None or self.client.authenticated) and self.query_one(
            "#positions-pane"
        ).display:
            self.query_one(DataTable).focus()
        else:
            frame = self._active_extra or self.query_one("#chart-pane", ChartFrame)
            frame.focus()

    def action_refresh(self) -> None:
        if self._switching:
            return
        if self.portfolio is not None:
            self.load_portfolio(restart_stream=True)
        elif self.client.authenticated and not self.positions_service.busy:
            self.load_positions(restart_stream=True)
        if not self.charts[0].busy:
            self.load_market()
        for panel in self.query(ExtraChart):
            if not panel.busy:
                panel.load_market()

    @work(group="history")
    async def load_history(self, chart: CandleChart) -> None:
        if (
            chart.history_exhausted
            or chart.history_busy
            or not chart.market
            or not chart.earliest_time()
        ):
            return
        chart.history_busy = True
        exchange = (
            chart.parent.session.hub.client.exchange.lower()
            if self.portfolio is not None and isinstance(chart.parent, ChartPanel)
            else self.client.exchange.lower()
        )
        if self.portfolio is not None and isinstance(chart.parent, ChartPanel):
            service = next(
                (
                    self._market_histories[key]
                    for key, hub in self._market_hubs.items()
                    if hub is chart.parent.session.hub
                ),
                self.history_service,
            )
        else:
            service = self._market_histories.get(exchange, self.history_service)
        generation = self._generation
        symbol, interval = chart.market.symbol, chart.market.interval
        try:
            bars = await service.older(symbol, interval, chart.earliest_time())
            if (
                generation != self._generation
                or not chart.is_mounted
                or not chart.market
                or (chart.market.symbol, chart.market.interval) != (symbol, interval)
            ):
                return
            if not bars or not chart.prepend_history(bars):
                chart.mark_history_exhausted()
        except ExchangeError as exc:
            logger.warning("Candle history unavailable (%s)", type(exc).__name__)
        finally:
            chart.history_busy = False
