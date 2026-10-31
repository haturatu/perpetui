"""Chart selection, adaptive placement and synchronized presentation actions.

The application supplies client-scoped services; this mixin owns workspace UI events.
"""

from __future__ import annotations

from math import lcm

from textual import on, work
from textual.containers import Grid
from textual.message_pump import MessagePump
from textual.widgets import DataTable, Select, Static

from perpetui.i18n import _
from perpetui.services.chart_session import ChartSession
from perpetui.ui.layout import LAYOUT_MODES, choose_layout
from perpetui.ui.screens.help import HelpScreen
from perpetui.ui.widgets.adaptive_grid import AdaptiveGrid
from perpetui.ui.widgets.chart import CandleChart
from perpetui.ui.widgets.chart_panel import ChartFrame, ChartPanel, ExtraChart
from perpetui.ui.widgets.search import SymbolSearch


class ChartWorkspace(MessagePump):
    @on(AdaptiveGrid.Resized)
    def chart_grid_resized(self) -> None:
        if not self._closing:
            self._apply_adaptive_layout()

    def _apply_adaptive_layout(self) -> None:
        if self.workspace.maximized:
            return
        grid = self.query_one("#chart-grid", Grid)
        frames = tuple(self.query(ChartFrame))
        if not frames or grid.size.width <= 0 or grid.size.height <= 0:
            return
        rows = choose_layout(
            len(frames), grid.size.width, grid.size.height, self.workspace.layout_mode
        )
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

    def _active_chart(self) -> CandleChart:
        return (
            self._active_extra.query_one(CandleChart)
            if self._active_extra
            else self.query_one("#chart", CandleChart)
        )

    def _active_frame(self) -> ChartPanel:
        return self._active_extra or self.query_one("#chart-pane", ChartPanel)

    def action_select_chart(self, index: int) -> None:
        if self._switching or self._closing or self.workspace.layout_busy:
            return
        if not 1 <= index <= self.workspace.chart_count:
            return
        frame_id = "chart-pane" if index == 1 else f"chart-pane-{index}"
        frame = self.query_one(f"#{frame_id}", ChartFrame)
        if self.workspace.maximized:
            self._select_chart(frame if isinstance(frame, ExtraChart) else None)
            self._apply_maximized_layout()
        frame.focus()

    def action_chart_count(self, count: int) -> None:
        if self._switching or self._closing:
            return
        self.workspace.desired_chart_count = min(9, max(1, count))
        if not self.workspace.layout_busy:
            self.workspace.layout_busy = True
            self._layout_worker = self.resize_charts()

    @work(group="layout")
    async def resize_charts(self) -> None:
        try:
            while True:
                count = self.workspace.desired_chart_count
                await self._reconcile_charts(count)
                self.workspace.chart_count = count
                selector = self.query_one("#chart-count", Select)
                with selector.prevent(Select.Changed):
                    selector.value = count
                self._title_chart()
                if self.workspace.maximized:
                    self._apply_maximized_layout()
                if count == self.workspace.desired_chart_count:
                    break
        finally:
            self.workspace.layout_busy = False

    def _default_chart_symbol(self, index: int) -> str:
        candidates = list(dict.fromkeys([p.symbol for p in self.positions]))
        known = self.query_one(SymbolSearch).symbols
        for coin in ("BTC", "ETH", "SOL"):
            symbol = self.client.normalize_symbol(coin)
            if symbol in known and symbol not in candidates:
                candidates.append(symbol)
        return candidates[index - 1] if len(candidates) >= index else self.symbol

    def _new_chart_session(self, index: int, symbol: str, interval: str) -> ChartSession:
        session = ChartSession(self.feed_hub, index, symbol, interval)
        self.charts.append(session)
        return session

    async def _reconcile_charts(self, count: int, settings: dict | None = None) -> None:
        for panel in list(self.query(ExtraChart)):
            if panel.index > count:
                if self._active_extra is panel:
                    self._select_chart(None)
                await panel.stop()
                await panel.remove()
                self.charts.remove(panel.session)
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
                        self._new_chart_session(index, symbol, interval),
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
            self.charts.remove(panel.session)

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
        if self.workspace.maximized:
            self._apply_maximized_layout()
        self._status()

    @on(ChartFrame.Selected)
    def chart_selected(self, event: ChartFrame.Selected) -> None:
        if not self._switching and event.frame in self.query(ChartFrame):
            self._select_chart(event.frame if isinstance(event.frame, ExtraChart) else None)

    @on(ChartPanel.Updated)
    def extra_chart_updated(self, event: ChartPanel.Updated) -> None:
        if event.panel.is_mounted and event.panel is self._active_frame():
            if event.panel.market:
                self._render_market(event.panel.market, force=True)
            else:
                self.query_one("#market-summary", Static).update(_("Loading market…"))
                self.query_one("#candle-info", Static).update("")
            self._status()

    @on(Select.Changed, "#chart-count")
    def chart_count_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, int) and event.value != self.workspace.desired_chart_count:
            self.action_chart_count(event.value)

    def action_layout(self) -> None:
        selector = self.query_one("#chart-layout", Select)
        selector.focus()
        selector.expanded = True

    @on(Select.Changed, "#chart-layout")
    def chart_layout_changed(self, event: Select.Changed) -> None:
        if event.value in LAYOUT_MODES:
            self.workspace.layout_mode = event.value
            self._layout_signature = None
            self._apply_adaptive_layout()

    def _title_chart(self) -> None:
        pane = self.query_one("#chart-pane", ChartPanel)
        pane.numbered = self.workspace.chart_count > 1
        pane.update_title()

    def action_candle_detail(self) -> None:
        self._active_chart().toggle_detail()
        self._active_frame().update_title()

    def action_price_zoom(self, direction: int) -> None:
        self._active_chart().zoom_price(direction)
        self._active_frame().update_title()

    def action_interval(self, direction: int) -> None:
        if self._switching:
            return
        values = list(self._intervals())
        interval = self._pending_interval or (
            self._active_extra.interval if self._active_extra else self.interval
        )
        index = (values.index(interval) + direction) % len(values)
        if self.workspace.all_timeframes:
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
        for panel in self.query(ChartPanel):
            if panel.interval != interval:
                panel.change(interval=interval)
        self._status()

    def action_toggle_timeframe_sync(self) -> None:
        if self._switching:
            return
        self.workspace.all_timeframes = not self.workspace.all_timeframes
        if self.workspace.all_timeframes:
            self._pending_interval = None
            interval = self._active_extra.interval if self._active_extra else self.interval
            self.set_interval_all(interval)
        self._status()

    def action_time_zoom(self, direction: int) -> None:
        if self._switching:
            return
        self._active_chart().zoom_time(direction)
        self._active_frame().update_title()
        self._status()

    def action_toggle_cursor_sync(self) -> None:
        if self._switching:
            return
        self.workspace.cursor_sync = not self.workspace.cursor_sync
        if self.workspace.cursor_sync:
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
            for panel in self.query(ChartPanel):
                panel.update_title()
        finally:
            self._syncing_cursor = False

    @on(CandleChart.CursorMoved)
    def candle_cursor_moved(self, event: CandleChart.CursorMoved) -> None:
        if self._switching or self._closing:
            return
        if event.chart is self._active_chart():
            self._active_frame().update_title()
            self._update_candle_info(event.chart)
        if self.workspace.cursor_sync and event.candle is not None:
            self._sync_cursor_time(event.candle.time)
        self._status()

    @on(CandleChart.HistoryRequested)
    def candle_history_requested(self, event: CandleChart.HistoryRequested) -> None:
        if not self._switching and not self._closing:
            self.load_history(event.chart)

    def action_maximize(self) -> None:
        if self._switching or self._closing:
            return
        self.workspace.maximized = not self.workspace.maximized
        self._apply_maximized_layout()
        self._status()

    def _apply_maximized_layout(self) -> None:
        self._layout_signature = None
        active = self._active_frame()
        for frame in self.query(ChartFrame):
            frame.display = not self.workspace.maximized or frame is active
        grid = self.query_one("#chart-grid", Grid)
        if self.workspace.maximized:
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
        self.workspace.positions_override = not self.query_one("#positions-pane").display
        self._update_positions_layout()

    def action_auto_positions(self) -> None:
        self.workspace.positions_override = None
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
