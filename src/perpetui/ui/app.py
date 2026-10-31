from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import aclosing, suppress
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from math import lcm

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Footer, Input, Select, Static

from perpetui.domain.history import CandleStore
from perpetui.domain.intervals import INTERVAL_SECONDS
from perpetui.domain.models import Market, Position, Positions
from perpetui.errors import CredentialsError, ExchangeError
from perpetui.exchanges.base import DataSource
from perpetui.i18n import _
from perpetui.ui.formatting import compact, price
from perpetui.ui.layout import LAYOUT_MODES, LayoutMode, choose_layout
from perpetui.ui.screens.help import HelpScreen
from perpetui.ui.widgets.adaptive_grid import AdaptiveGrid
from perpetui.ui.widgets.chart import CandleChart
from perpetui.ui.widgets.chart_panel import ChartFrame, ExtraChart
from perpetui.ui.widgets.chart_plot import ACCENT, DOWN, MUTED, UP
from perpetui.ui.widgets.search import SymbolSearch

logger = logging.getLogger("perpetui")


def signed(value: Decimal | None, suffix: str = "") -> Text:
    if value is None:
        return Text("—", style=MUTED)
    return Text(f"{value:+,.2f}{suffix}", style=UP if value >= 0 else DOWN)


class TradingApp(App[None]):
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
        self._all_timeframes = False
        self._cursor_sync = False
        self._maximized = False
        self._layout_mode: LayoutMode = "auto"
        self._layout_signature: tuple[tuple[int, ...], tuple[ChartFrame, ...]] | None = None
        self._positions_override: bool | None = None
        self._all_interval_timer = None
        self._pending_interval: str | None = None
        self._syncing_cursor = False
        self.interval = interval
        self.refresh_seconds = refresh_seconds
        self._reset_data(symbol)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "select_chart" and isinstance(getattr(self, "focused", None), (Input, Select)):
            return False
        return True

    def _intervals(self) -> tuple[str, ...]:
        return tuple(getattr(self.client, "intervals", tuple(INTERVAL_SECONDS)))

    def _reset_data(self, symbol: str | None = None) -> None:
        if self._all_interval_timer is not None:
            self._all_interval_timer.stop()
            self._all_interval_timer = None
        self._pending_interval = None
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
        self._history_cache: dict[tuple[str, str], CandleStore] = {}

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
                value=self._layout_mode,
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
        self._update_positions_layout()
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
        if self.query("#positions-pane"):
            self._update_positions_layout()

    @on(AdaptiveGrid.Resized)
    def chart_grid_resized(self) -> None:
        if not self._closing:
            self._apply_adaptive_layout()

    def _apply_adaptive_layout(self) -> None:
        if self._maximized:
            return
        grid = self.query_one("#chart-grid", Grid)
        frames = tuple(self.query(ChartFrame))
        if not frames or grid.size.width <= 0 or grid.size.height <= 0:
            return
        rows = choose_layout(len(frames), grid.size.width, grid.size.height, self._layout_mode)
        signature = (rows, frames)
        if self._layout_signature == signature:
            return
        columns = lcm(*rows)
        grid.styles.grid_size_columns = columns
        grid.styles.grid_size_rows = len(rows)
        grid.styles.grid_columns = " ".join(["1fr"] * columns)
        grid.styles.grid_rows = " ".join(["1fr"] * len(rows))
        offset = 0
        for count in rows:
            for frame in frames[offset : offset + count]:
                frame.styles.column_span = columns // count
                frame.styles.row_span = 1
            offset += count
        self._layout_signature = signature

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

    def _active_frame(self) -> ChartFrame:
        return self._active_extra or self.query_one("#chart-pane", ChartFrame)

    def action_select_chart(self, index: int) -> None:
        if self._switching or self._closing or self._layout_busy:
            return
        if not 1 <= index <= self._chart_count:
            return
        frame_id = "chart-pane" if index == 1 else f"chart-pane-{index}"
        frame = self.query_one(f"#{frame_id}", ChartFrame)
        if self._maximized:
            self._select_chart(frame if isinstance(frame, ExtraChart) else None)
            self._apply_maximized_layout()
        frame.focus()

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
                if self._maximized:
                    self._apply_maximized_layout()
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
        self._apply_maximized_layout()

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
        if self._maximized:
            self._apply_maximized_layout()
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

    def action_layout(self) -> None:
        selector = self.query_one("#chart-layout", Select)
        selector.focus()
        selector.expanded = True

    @on(Select.Changed, "#chart-layout")
    def chart_layout_changed(self, event: Select.Changed) -> None:
        if event.value in LAYOUT_MODES:
            self._layout_mode = event.value
            self._layout_signature = None
            self._apply_adaptive_layout()

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
        mode = chart.mode_label()
        self.query_one("#chart-pane").border_title = (
            f" {'1 · ' if self._chart_count > 1 else ''}{self.symbol.replace('_', ' / ')} · "
            f"{self.interval} · {_('PERPETUAL')} · "
            f"{style_label}{zoom_label} · {mode} "
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
        self._update_positions_layout()

    def _update_positions_layout(self) -> None:
        pane = self.query_one("#positions-pane")
        table = self.query_one(DataTable)
        count = len(self.positions)
        visible = self.client.authenticated and (
            self._positions_override
            if self._positions_override is not None
            else bool(count or self._positions_error or not self._initial_selection)
        )
        max_height = min(12, max(5, self.size.height // 4))
        # A horizontal scrollbar also consumes a table row on narrow terminals.
        scrollbar_height = table.scrollbar_size_horizontal if count else 0
        pane.styles.height = min(count + 4 + scrollbar_height if count else 4, max_height)
        # Move focus before hiding its parent so Textual doesn't select another control.
        if not visible and self.focused is table:
            self._active_frame().focus()
        pane.display = visible

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
        if self.client.authenticated:
            message += " · " + _("{count} positions", count=len(self.positions))
        flags = []
        if self._all_timeframes:
            flags.append(_("ALL TF: ON"))
        if self._cursor_sync:
            flags.append(_("CURSOR SYNC: ON"))
        if self._maximized:
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
        if self._all_timeframes:
            self._pending_interval = None
            self.set_interval_all(event.value)
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
        if self.client.authenticated and self.query_one("#positions-pane").display:
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
        interval = self._pending_interval or (
            self._active_extra.interval if self._active_extra else self.interval
        )
        index = (values.index(interval) + direction) % len(values)
        if self._all_timeframes:
            self._schedule_interval_all(values[index])
            return
        self.query_one("#timeframe", Select).value = values[index]

    def action_interval_all(self, direction: int) -> None:
        if self._switching:
            return
        values = list(self._intervals())
        interval = self._pending_interval or (
            self._active_extra.interval if self._active_extra else self.interval
        )
        index = (values.index(interval) + direction) % len(values)
        self._schedule_interval_all(values[index])

    def _schedule_interval_all(self, interval: str) -> None:
        self._pending_interval = interval
        if self._all_interval_timer is not None:
            self._all_interval_timer.stop()
        self._all_interval_timer = self.set_timer(0.2, lambda: self.set_interval_all(interval))

    def set_interval_all(self, interval: str) -> None:
        if self._switching or self._closing or interval not in self._intervals():
            self._pending_interval = None
            return
        self._pending_interval = None
        selector = self.query_one("#timeframe", Select)
        with selector.prevent(Select.Changed):
            selector.value = interval
        if self.interval != interval:
            self.interval = interval
            self.load_market()
        for panel in self.query(ExtraChart):
            if panel.interval != interval:
                panel.change(interval=interval)
        self._status()

    def action_toggle_timeframe_sync(self) -> None:
        if self._switching:
            return
        self._all_timeframes = not self._all_timeframes
        if self._all_timeframes:
            self._pending_interval = None
            interval = self._active_extra.interval if self._active_extra else self.interval
            self.set_interval_all(interval)
        self._status()

    def action_time_zoom(self, direction: int) -> None:
        if self._switching:
            return
        self._active_chart().zoom_time(direction)
        if self._active_extra:
            self._active_extra.update_title()
        else:
            self._title_chart()
        self._status()

    def action_toggle_cursor_sync(self) -> None:
        if self._switching:
            return
        self._cursor_sync = not self._cursor_sync
        if self._cursor_sync:
            candle = self._active_chart().selected_candle()
            if candle is not None:
                self._sync_cursor_time(candle.time)
        self._status()

    def _sync_cursor_time(self, timestamp: int) -> None:
        if self._syncing_cursor:
            return
        self._syncing_cursor = True
        try:
            for chart in self.query(CandleChart):
                if chart is not self._active_chart():
                    chart.select_time(timestamp, notify=False)
            for panel in self.query(ExtraChart):
                panel.update_title()
        finally:
            self._syncing_cursor = False

    @on(CandleChart.CursorMoved)
    def candle_cursor_moved(self, event: CandleChart.CursorMoved) -> None:
        if self._switching or self._closing:
            return
        if event.chart is self._active_chart():
            if self._active_extra:
                self._active_extra.update_title()
            else:
                self._title_chart()
            self._update_candle_info(event.chart)
        if self._cursor_sync and event.candle is not None:
            self._sync_cursor_time(event.candle.time)
        self._status()

    @on(CandleChart.HistoryRequested)
    def candle_history_requested(self, event: CandleChart.HistoryRequested) -> None:
        if not self._switching and not self._closing:
            self.load_history(event.chart)

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
        client, generation = self.client, self._generation
        symbol, interval = chart.market.symbol, chart.market.interval
        cache = self._history_cache.setdefault((symbol, interval), CandleStore())
        try:
            earliest = chart.earliest_time() or 0
            cached = [bar for bar in cache.all() if bar.time < earliest]
            if cached and not chart.prepend_history(cached):
                chart.mark_history_exhausted()
                return
            if cached:
                earliest = chart.earliest_time() or earliest
            bars = await client.history(symbol, interval, earliest)
            cache.merge_live(bars)
            if (
                generation != self._generation
                or not chart.is_mounted
                or not chart.market
                or (chart.market.symbol, chart.market.interval) != (symbol, interval)
            ):
                return
            older = [bar for bar in bars if bar.time < earliest]
            if not older or not chart.prepend_history(older):
                chart.mark_history_exhausted()
        except ExchangeError as exc:
            logger.warning("Candle history unavailable (%s)", type(exc).__name__)
        finally:
            chart.history_busy = False

    def action_maximize(self) -> None:
        if self._switching or self._closing:
            return
        self._maximized = not self._maximized
        self._apply_maximized_layout()
        self._status()

    def _apply_maximized_layout(self) -> None:
        self._layout_signature = None
        active = self._active_frame()
        for frame in self.query(ChartFrame):
            frame.display = not self._maximized or frame is active
        grid = self.query_one("#chart-grid", Grid)
        if self._maximized:
            grid.styles.grid_size_columns = 1
            grid.styles.grid_size_rows = 1
            grid.styles.grid_columns = "1fr"
            grid.styles.grid_rows = "1fr"
            for frame in self.query(ChartFrame):
                frame.styles.column_span = 1
                frame.styles.row_span = 1
            return
        self._apply_adaptive_layout()

    def action_toggle_positions(self) -> None:
        if not self.client.authenticated or self._switching:
            return
        self._positions_override = not self.query_one("#positions-pane").display
        self._update_positions_layout()

    def action_auto_positions(self) -> None:
        self._positions_override = None
        self._update_positions_layout()

    def action_help(self) -> None:
        rows = [
            ("1–9", _("Select chart")),
            ("Ctrl+1–9", _("Chart count")),
            ("l", _("Chart layout")),
            ("← / →", _("Previous / next candle")),
            ("Shift+← / →", _("Move 5 candles")),
            ("Home / End", _("Oldest candle / live")),
            ("PageUp / PageDown", _("Move one screen")),
            ("[ / ]", _("Timeframe of the selected chart")),
            ("Shift+[ / ]", _("Apply timeframe to all charts")),
            ("a", _("Toggle all-chart timeframe sync")),
            ("s", _("Toggle cursor time sync")),
            ("+ / - / 0", _("Price zoom / reset")),
            ("Ctrl+↑ / ↓", _("Fewer / more candles")),
            ("v", _("Candle style")),
            ("z", _("Maximize the selected chart")),
            ("p", _("Show / hide positions")),
            ("Shift+P", _("Automatically size and hide positions")),
            ("/", _("Symbol search")),
            ("e", _("Exchange")),
            ("r", _("Refresh")),
            ("Ctrl+P", _("Command palette")),
            ("q", _("Quit")),
        ]
        self.push_screen(HelpScreen(rows))

    def action_next_position(self, direction: int) -> None:
        table = self.query_one(DataTable)
        if self.positions:
            table.move_cursor(row=(table.cursor_row + direction) % len(self.positions))
