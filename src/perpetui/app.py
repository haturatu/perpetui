from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from contextlib import aclosing, suppress
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Footer, Input, Select, Static

from .chart import ACCENT, DOWN, MUTED, UP, CandleChart
from .chart_panel import ChartFrame, ExtraChart
from .client import DataSource
from .credentials import CredentialsError
from .errors import ExchangeError
from .i18n import _
from .models import INTERVALS, Market, Position, Positions, compact, price
from .search import SymbolSearch

logger = logging.getLogger("perpetui")


def signed(value: Decimal | None, suffix: str = "") -> Text:
    if value is None:
        return Text("—", style=MUTED)
    return Text(f"{value:+,.2f}{suffix}", style=UP if value >= 0 else DOWN)


class TradingApp(App[None]):
    TITLE = "perpetui"
    CSS = """
    Screen { background: #080e18; color: #e4edf8; }
    #topbar { height: 3; padding: 0 1; align-vertical: middle;
        border-bottom: solid #233149; }
    #brand { width: 1fr; color: #e4edf8; text-style: bold; }
    #connection { width: auto; color: #788ba9; }
    #exchange { width: 18; height: 1; margin: 0 1; }
    #chart-count { width: 7; height: 1; margin: 0 1; }
    #exchange > SelectCurrent, #chart-count > SelectCurrent {
        height: 1; border: none; padding: 0 1; }
    #market-summary { height: 3; padding: 0 2; content-align: left middle; }
    #toolbar { height: 3; padding: 0 1; }
    Input { width: 24; height: 3; border: tall #233149; background: #101a2a; }
    Input:focus { border: tall #7aa2ff; }
    Select { width: 13; margin-left: 1; }
    SelectCurrent { border: tall #233149; background: #101a2a; }
    #candle-info { width: 1fr; padding: 1 1; color: #788ba9; }
    #chart-grid { height: 1fr; min-height: 8; margin: 0 1;
        grid-size: 1 1; grid-columns: 1fr; grid-rows: 1fr; grid-gutter: 0 1; }
    .chart-frame { height: 1fr; min-height: 0;
        border: round #233149; padding: 0 1; border-title-color: #aebfd8; }
    .chart-frame.active { border: round #7aa2ff; }
    CandleChart { height: 1fr; }
    #positions-pane { height: 10; margin: 0 1; border: round #233149;
        border-title-color: #aebfd8; }
    #positions-summary { height: 1; padding: 0 1; color: #788ba9; }
    DataTable { height: 1fr; background: #080e18; }
    DataTable > .datatable--header { background: #101a2a; color: #788ba9; text-style: none; }
    DataTable > .datatable--cursor { background: #192d49; color: #e4edf8; }
    DataTable > .datatable--hover { background: #112036; }
    #empty { height: 1fr; content-align: center middle; color: #788ba9; }
    #status { height: 1; padding: 0 2; color: #788ba9; }
    #status.error { color: #fb7185; }
    Footer { background: #101a2a; }
    Footer > .footer--key { background: #192d49; color: #b6cdff; }
    Screen.compact #positions-pane { height: 7; }
    Screen.compact #topbar { height: 1; border: none; }
    Screen.compact #market-summary { height: 2; }
    Screen.compact #candle-info { display: none; }
    """
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
        Binding("plus,equals", "price_zoom(1)", "Price +"),
        Binding("minus", "price_zoom(-1)", "Price −"),
        Binding("0", "price_zoom(0)", "Reset"),
        Binding("v", "candle_detail", "Style"),
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
        self._chart_count = self._desired_chart_count = 1
        self._layout_busy = False
        self._layout_worker = None
        self._active_extra: ExtraChart | None = None
        self.interval = interval
        self.refresh_seconds = refresh_seconds
        self._reset_data(symbol)

    def _intervals(self) -> tuple[str, ...]:
        return tuple(getattr(self.client, "intervals", tuple(INTERVALS)))

    def _reset_data(self, symbol: str | None = None) -> None:
        intervals = self._intervals()
        if self.interval not in intervals:
            self.interval = "15m" if "15m" in intervals else intervals[0]
        self.symbol = symbol or self.client.default_symbol
        self.positions: list[Position] = []
        self.market: Market | None = None
        self._manual_symbol = symbol is not None
        self._initial_selection = False
        self._positions_busy = False
        self._market_busy = False
        self._selected_id: str | None = None
        self._positions_error = ""
        self._market_error = ""
        self._quote_warning = ""
        self._market_updated = ""
        self._positions_updated = ""
        self._stream_task: asyncio.Task[None] | None = None
        self._stream_state = "connecting" if self.client.streaming else "REST"
        self._positions_stream_task: asyncio.Task[None] | None = None
        self._positions_stream_state = "connecting" if self.client.position_streaming else "REST"
        self._positions_revision = 0

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
        with Grid(id="chart-grid"):
            with ChartFrame(id="chart-pane", classes="chart-frame active"):
                yield CandleChart(id="chart")
        with Vertical(id="positions-pane"):
            yield Static("", id="positions-summary")
            yield DataTable(id="positions", cursor_type="row", zebra_stripes=True)
            yield Static(_("Loading open positions…"), id="empty")
        yield Static(_("Connecting…"), id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#positions-pane").border_title = " " + _("OPEN POSITIONS") + " "
        self._setup_table()
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
        self.query_one("#positions-pane").display = self.client.authenticated
        table = self.query_one(DataTable)
        with table.prevent(DataTable.RowHighlighted):
            table.clear(columns=True)
        table.add_columns(
            *map(
                _,
                (
                    "Symbol",
                    "Side",
                    self.client.quantity_label,
                    "Entry",
                    "Mark",
                    "uPnL",
                    "ROE",
                    "Liq. price",
                    "Lev.",
                    "Margin",
                ),
            )
        )

    def _load_initial_data(self) -> None:
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
        client, generation = self.client, self._generation
        search = self.query_one(SymbolSearch)
        try:
            symbols = await client.symbols()
            if generation == self._generation and self._view_ready():
                search.set_symbols(symbols)
        except ExchangeError as exc:
            if generation != self._generation:
                return
            logger.warning("Symbol suggestions unavailable (%s)", type(exc).__name__)
            search.set_symbols([], unavailable=True)

    def on_resize(self) -> None:
        self.screen.set_class(self.size.height < 32, "compact")

    async def on_unmount(self) -> None:
        self._closing = True
        self._generation += 1
        workers = [
            worker
            for group in ("exchange", "layout")
            for worker in self.workers.cancel_group(self, group)
        ]
        await asyncio.gather(*(worker.wait() for worker in workers), return_exceptions=True)
        await self._stop_stream()
        await self._stop_positions_stream()
        await asyncio.gather(*(panel.stop() for panel in self.query(ExtraChart)))
        await self.client.close()

    def _active_chart(self) -> CandleChart:
        return (
            self._active_extra.query_one(CandleChart)
            if self._active_extra
            else self.query_one("#chart", CandleChart)
        )

    def action_chart_count(self, count: int) -> None:
        if self._switching or self._closing:
            return
        self._desired_chart_count = min(9, max(1, count))
        if not self._layout_busy:
            self._layout_busy = True
            self._layout_worker = self.resize_charts()

    @work(group="layout")
    async def resize_charts(self) -> None:
        try:
            while True:
                count = self._desired_chart_count
                await self._reconcile_charts(count)
                self._chart_count = count
                selector = self.query_one("#chart-count", Select)
                with selector.prevent(Select.Changed):
                    selector.value = count
                self._title_chart()
                if count == self._desired_chart_count:
                    break
        finally:
            self._layout_busy = False

    def _default_chart_symbol(self, index: int) -> str:
        candidates = list(dict.fromkeys([p.symbol for p in self.positions]))
        known = self.query_one(SymbolSearch).symbols
        for coin in ("BTC", "ETH", "SOL"):
            symbol = self.client.normalize_symbol(coin)
            if symbol in known and symbol not in candidates:
                candidates.append(symbol)
        return candidates[index - 1] if len(candidates) >= index else self.symbol

    def _entry_for(self, symbol: str) -> Decimal | None:
        selected = next((p for p in self.positions if p.id == self._selected_id), None)
        if selected and selected.symbol == symbol:
            return selected.entry
        return next((p.entry for p in self.positions if p.symbol == symbol), None)

    async def _reconcile_charts(self, count: int, settings: dict | None = None) -> None:
        for panel in list(self.query(ExtraChart)):
            if panel.index > count:
                if self._active_extra is panel:
                    self._select_chart(None)
                await panel.stop()
                await panel.remove()
        existing = {panel.index for panel in self.query(ExtraChart)}
        supported = self._intervals()
        for index in range(2, count + 1):
            if index not in existing:
                symbol = self._default_chart_symbol(index)
                interval, detail, zoom = (settings or {}).get(
                    index,
                    (
                        self.interval,
                        self.query_one("#chart", CandleChart).detail,
                        1,
                    ),
                )
                if interval not in supported:
                    interval = self.interval
                await self.query_one("#chart-grid", Grid).mount(
                    ExtraChart(
                        self.client,
                        index,
                        symbol,
                        interval,
                        entry=self._entry_for(symbol),
                        detail=detail,
                        zoom=zoom,
                        autoload=self._initial_selection or not self.client.authenticated,
                    )
                )
        columns = math.ceil(math.sqrt(count))
        grid = self.query_one("#chart-grid", Grid)
        grid.styles.grid_size_columns = columns
        grid.styles.grid_size_rows = math.ceil(count / columns)

    async def _remove_extra_charts(self) -> None:
        self._select_chart(None)
        for panel in list(self.query(ExtraChart)):
            await panel.stop()
            await panel.remove()

    def _select_chart(self, panel: ExtraChart | None) -> None:
        self._active_extra = panel
        for frame in self.query(ChartFrame):
            frame.set_class(frame is panel if panel else frame.id == "chart-pane", "active")
        selector = self.query_one("#timeframe", Select)
        with selector.prevent(Select.Changed):
            selector.value = panel.interval if panel else self.interval
        market = panel.market if panel else self.market
        if market:
            self._render_market(market, force=True)
        else:
            self.query_one("#market-summary", Static).update(_("Loading market…"))
            self.query_one("#candle-info", Static).update("")
        self._status()

    @on(ChartFrame.Selected)
    def chart_selected(self, event: ChartFrame.Selected) -> None:
        if not self._switching and event.frame in self.query(ChartFrame):
            self._select_chart(event.frame if isinstance(event.frame, ExtraChart) else None)

    @on(ExtraChart.Updated)
    def extra_chart_updated(self, event: ExtraChart.Updated) -> None:
        if self._active_extra is event.panel and event.panel.is_mounted:
            if event.panel.market:
                self._render_market(event.panel.market, force=True)
            else:
                self.query_one("#market-summary", Static).update(_("Loading market…"))
                self.query_one("#candle-info", Static).update("")
            self._status()

    @on(Select.Changed, "#chart-count")
    def chart_count_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, int) and event.value != self._desired_chart_count:
            self.action_chart_count(event.value)

    def action_exchange(self) -> None:
        selector = self.query_one("#exchange", Select)
        if not selector.disabled:
            selector.focus()
            selector.expanded = True

    @on(Select.Changed, "#exchange")
    def exchange_changed(self, event: Select.Changed) -> None:
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
                assert self._client_factory is not None
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
            await self._stop_stream()
            await self._stop_positions_stream()
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
            await self._reconcile_charts(self._chart_count, settings)
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

    async def _stop_positions_stream(self) -> None:
        if self._positions_stream_task is not None:
            self._positions_stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._positions_stream_task
            self._positions_stream_task = None

    async def _consume_positions_stream(self) -> None:
        client, generation = self.client, self._generation
        try:
            async with aclosing(client.stream_positions()) as stream:
                async for event in stream:
                    if generation != self._generation or not self._view_ready():
                        return
                    self._positions_stream_state = event.state
                    if event.positions is not None:
                        self._set_positions(event.positions)
                    self._status()
        except ExchangeError as exc:
            logger.warning("Positions stream unavailable (%s); using REST", type(exc).__name__)
        finally:
            if generation == self._generation:
                self._positions_stream_state = "reconnecting"
                self._status()

    async def _stop_stream(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
            self._stream_task = None

    async def _consume_stream(self, seed: Market) -> None:
        client, generation = self.client, self._generation
        key = (seed.symbol, seed.interval)
        try:
            async with aclosing(client.stream_market(seed)) as stream:
                async for event in stream:
                    if (
                        generation != self._generation
                        or not self._view_ready()
                        or key != (self.symbol, self.interval)
                    ):
                        return
                    self._stream_state = event.state
                    if event.market is not None:
                        self.market = event.market
                        self._market_error = ""
                        self._market_updated = datetime.now().strftime("%H:%M:%S")
                        self.query_one("#chart", CandleChart).set_market(
                            event.market, self._entry()
                        )
                        self._render_market(event.market)
                    self._status()
        except ExchangeError as exc:
            self._stream_state = "reconnecting"
            logger.warning("Market stream unavailable (%s); using REST", type(exc).__name__)
            self._status()

    def _title_chart(self) -> None:
        chart = self.query_one("#chart", CandleChart)
        zoom = chart.price_zoom
        zoom_label = f" · {_('PRICE')} ×{zoom}" if zoom > 1 else ""
        style_label = _("FINE") if chart.detail else _("SOLID")
        self.query_one("#chart-pane").border_title = (
            f" {'1 · ' if self._chart_count > 1 else ''}{self.symbol.replace('_', ' / ')} · "
            f"{self.interval} · {_('PERPETUAL')} · "
            f"{style_label}{zoom_label} "
        )

    def action_candle_detail(self) -> None:
        self._active_chart().toggle_detail()
        self._active_extra.update_title() if self._active_extra else self._title_chart()

    def action_price_zoom(self, direction: int) -> None:
        self._active_chart().zoom_price(direction)
        self._active_extra.update_title() if self._active_extra else self._title_chart()

    def _entry(self) -> Decimal | None:
        selected = next((p for p in self.positions if p.id == self._selected_id), None)
        if selected and selected.symbol == self.symbol:
            return selected.entry
        return next((p.entry for p in self.positions if p.symbol == self.symbol), None)

    def refresh_data(self) -> None:
        if self._switching or self._closing:
            return
        for panel in self.query(ExtraChart):
            panel.refresh_data()
        if (
            self.client.authenticated
            and not self._positions_busy
            and self._positions_stream_state != "live"
        ):
            self.load_positions()
        if (
            (self._initial_selection or self.market is not None)
            and not self._market_busy
            and self._stream_state != "live"
        ):
            self.load_market(restart_stream=False)

    @work(exclusive=True, group="positions")
    async def load_positions(self, restart_stream: bool = False) -> None:
        if not self._view_ready() or not self.client.authenticated:
            return
        client, generation = self.client, self._generation
        self._positions_busy = True
        if restart_stream and self.client.position_streaming:
            await self._stop_positions_stream()
            self._positions_stream_state = "connecting"
        revision = self._positions_revision
        try:
            snapshot = await client.positions()
            # A socket can recover while a fallback REST request is in flight.
            if generation != self._generation or revision != self._positions_revision:
                return
            self._set_positions(snapshot)
            if self.client.position_streaming and (
                self._positions_stream_task is None or self._positions_stream_task.done()
            ):
                self._positions_stream_task = asyncio.create_task(self._consume_positions_stream())
        except ExchangeError as exc:
            if generation != self._generation or revision != self._positions_revision:
                return
            self._positions_error = _("Positions unavailable: {error}", error=exc)
            logger.warning("%s", self._positions_error)
            if not self._initial_selection:
                self.load_market()
                self._start_extra_defaults()
            self._render_positions()
        finally:
            if generation == self._generation:
                self._positions_busy = False
                self._status()

    def _set_positions(self, snapshot: Positions) -> None:
        if not self._view_ready():
            return
        self._positions_revision += 1
        self.positions = snapshot.items
        self._quote_warning = " · ".join(snapshot.warnings)
        self._positions_error = ""
        self._positions_updated = datetime.now().strftime("%H:%M:%S")
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
        for panel in self.query(ExtraChart):
            panel.set_entry(self._entry_for(panel.symbol))
        chart = self.query_one("#chart", CandleChart)
        entry = self._entry()
        if chart.entry != entry:
            chart.entry = entry
            chart.refresh()

    def _start_extra_defaults(self) -> None:
        for panel in self.query(ExtraChart):
            if panel.manual_symbol:
                continue
            panel.set_entry(self._entry_for(self._default_chart_symbol(panel.index)))
            panel.change(symbol=self._default_chart_symbol(panel.index))

    @work(exclusive=True, group="market")
    async def load_market(self, restart_stream: bool = True) -> None:
        if not self._view_ready():
            return
        client, generation = self.client, self._generation
        self._market_busy = True
        symbol, interval = self.symbol, self.interval
        if restart_stream and self.client.streaming:
            await self._stop_stream()
            self._stream_state = "connecting"
        if not self._view_ready() or generation != self._generation:
            return
        self._title_chart()
        chart = self.query_one("#chart", CandleChart)
        if self.market is None or (self.market.symbol, self.market.interval) != (symbol, interval):
            chart.market = None
            chart.message = _(
                "Loading {symbol} · {interval}…",
                symbol=symbol.replace("_", " / "),
                interval=interval,
            )
            chart.refresh()
            self.query_one("#market-summary", Static).update(Text(chart.message))
            self.query_one("#candle-info", Static).update("")
        try:
            market = await client.market(symbol, interval)
            if generation != self._generation or not self._view_ready():
                return
            # A fallback request can finish after the socket has recovered.
            if not restart_stream and self._stream_state == "live":
                return
            self.market = market
            self.symbol = market.symbol
            self._title_chart()
            self._market_error = ""
            self._market_updated = datetime.now().strftime("%H:%M:%S")
            chart.set_market(market, self._entry())
            self._render_market(market)
            if self.client.streaming and (self._stream_task is None or self._stream_task.done()):
                self._stream_task = asyncio.create_task(self._consume_stream(market))
        except ExchangeError as exc:
            if generation != self._generation:
                return
            if not restart_stream and self._stream_state == "live":
                return
            self._market_error = _("Chart unavailable: {error}", error=exc)
            logger.warning("%s", self._market_error)
            if chart.market is None:
                chart.message = _("Chart unavailable · press r to retry")
                chart.refresh()
        finally:
            if generation == self._generation:
                self._market_busy = False
                self._status()

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
        bar = market.candles[-1]
        self.query_one("#candle-info", Static).update(
            Text(
                f"O {price(bar.open)}  H {price(bar.high)}  "
                f"L {price(bar.low)}  C {price(bar.close)}"
            )
        )

    def _render_positions(self) -> None:
        table = self.query_one(DataTable)
        previous_id = self._selected_id
        same_rows = [row.value for row in table.rows] == [p.id for p in self.positions]
        with table.prevent(DataTable.RowHighlighted):
            if not same_rows:
                table.clear()
            for row_index, p in enumerate(self.positions):
                cells = (
                    Text(p.symbol.replace("_", "/"), style=ACCENT),
                    Text(_(p.side), style=UP if p.side == "LONG" else DOWN),
                    f"{p.contracts:,.0f}" if p.contracts == int(p.contracts) else str(p.contracts),
                    price(p.entry),
                    price(p.mark),
                    signed(p.pnl),
                    signed(p.roe, "%"),
                    price(p.liquidation) if p.liquidation > 0 else "—",
                    f"{p.leverage}x",
                    f"{price(p.margin)} {p.settlement}",
                )
                if same_rows:
                    for column_index, value in enumerate(cells):
                        coordinate = Coordinate(row_index, column_index)
                        if table.get_cell_at(coordinate) != value:
                            table.update_cell_at(coordinate, value, update_width=True)
                else:
                    table.add_row(*cells, key=p.id)
            index = next((i for i, p in enumerate(self.positions) if p.id == previous_id), 0)
            if self.positions:
                self._selected_id = self.positions[index].id
                if not same_rows:
                    table.move_cursor(row=index)
            else:
                self._selected_id = None
        table.display = bool(self.positions)
        empty = self.query_one("#empty", Static)
        empty.display = not self.positions
        if self._positions_error:
            empty.update(_("Positions unavailable · press r to retry"))
        elif not self.client.authenticated:
            empty.update(_(self.client.public_hint))
        else:
            empty.update(
                _(
                    "No open positions · {symbol} is the default chart",
                    symbol=self.client.default_symbol.replace("_", " / "),
                )
            )
        summary = Text(
            " " + _("{count} positions  ·  ↑ ↓ / j k select chart", count=len(self.positions)),
            style=MUTED,
        )
        if self.positions:
            totals: dict[str, Decimal] = {}
            for p in self.positions:
                if p.pnl is not None:
                    totals[p.settlement] = totals.get(p.settlement, Decimal(0)) + p.pnl
            for settlement, pnl in totals.items():
                summary.append("  ·  " + _("uPnL") + " ")
                summary.append_text(signed(pnl, " " + settlement))
        self.query_one("#positions-summary", Static).update(summary)

    def _status(self) -> None:
        if not self._view_ready() or not self.query("#status"):
            return
        status = self.query_one("#status", Static)
        self.query_one("#positions-pane").border_title = f" {_('OPEN POSITIONS')} "
        market_error = self._active_extra.error if self._active_extra else self._market_error
        errors = [error for error in (self._positions_error, market_error) if error]
        status.set_class(bool(errors), "error")
        if errors:
            message = _("STALE") + " · " + " · ".join(errors)
        else:
            message = _(
                "Chart {chart_time}  ·  Positions {positions_time}  ·  Local time"
                if self.client.authenticated
                else "Chart {chart_time}  ·  Local time",
                chart_time=(
                    self._active_extra.updated if self._active_extra else self._market_updated
                )
                or "…",
                positions_time=self._positions_updated or "…",
            )
        if self._quote_warning:
            message += " · " + self._quote_warning
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
            if self._active_extra:
                self._active_extra.manual_symbol = True
                self._active_extra.set_entry(position.entry)
                if self._active_extra.symbol != position.symbol:
                    self._active_extra.change(symbol=position.symbol)
                return
            if self.symbol != position.symbol:
                self.symbol = position.symbol
                self.load_market()
            else:
                chart = self.query_one("#chart", CandleChart)
                chart.entry = position.entry
                chart.refresh()

    @on(SymbolSearch.Chosen)
    def submit_symbol(self, event: SymbolSearch.Chosen) -> None:
        if self._switching:
            return
        try:
            symbol = self.client.normalize_symbol(event.value)
        except ValueError as exc:
            self.notify(_(str(exc)), severity="warning")
            return
        if self._active_extra:
            self._active_extra.manual_symbol = True
            self._active_extra.set_entry(self._entry_for(symbol))
            self._active_extra.change(symbol=symbol)
            self.query_one(Input).value = ""
            self.action_positions()
            return
        self.symbol = symbol
        self._manual_symbol = True
        self.query_one(Input).value = ""
        self.action_positions()
        self.load_market()

    @on(SymbolSearch.Cancelled)
    def cancel_search(self) -> None:
        self.action_positions()

    @on(Select.Changed, "#timeframe")
    def timeframe_changed(self, event: Select.Changed) -> None:
        if self._switching or event.value not in self._intervals():
            return
        if self._active_extra:
            if isinstance(event.value, str) and event.value != self._active_extra.interval:
                self._active_extra.change(interval=event.value)
            return
        if isinstance(event.value, str) and event.value != self.interval:
            self.interval = event.value
            self.load_market()

    def action_search(self) -> None:
        if self._switching:
            return
        self.query_one(Input).focus()

    def action_positions(self) -> None:
        if self.client.authenticated:
            self.query_one(DataTable).focus()
        else:
            frame = self._active_extra or self.query_one("#chart-pane", ChartFrame)
            frame.focus()

    def action_refresh(self) -> None:
        if self._switching:
            return
        if self.client.authenticated and not self._positions_busy:
            self.load_positions(restart_stream=True)
        if not self._market_busy:
            self.load_market()
        for panel in self.query(ExtraChart):
            if not panel.busy:
                panel.load_market()

    def action_interval(self, direction: int) -> None:
        if self._switching:
            return
        values = list(self._intervals())
        interval = self._active_extra.interval if self._active_extra else self.interval
        index = (values.index(interval) + direction) % len(values)
        self.query_one("#timeframe", Select).value = values[index]

    def action_next_position(self, direction: int) -> None:
        table = self.query_one(DataTable)
        if self.positions:
            table.move_cursor(row=(table.cursor_row + direction) % len(self.positions))
