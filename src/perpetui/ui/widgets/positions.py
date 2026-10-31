"""Position-table rendering and sizing, with selection kept stable on updates."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static

from perpetui.domain.models import Position
from perpetui.exchanges.base import DataSource
from perpetui.i18n import _
from perpetui.ui.formatting import price, signed
from perpetui.ui.theme import ACCENT, DOWN, MUTED, UP


class PositionsPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("", id="positions-summary")
        yield DataTable(id="positions", cursor_type="row", zebra_stripes=True)
        yield Static(_("Loading open positions…"), id="empty")

    def setup(self, client: DataSource) -> None:
        table = self.query_one(DataTable)
        with table.prevent(DataTable.RowHighlighted):
            table.clear(columns=True)
        table.add_columns(
            *map(
                _,
                (
                    "Symbol",
                    "Side",
                    client.quantity_label,
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

    def render_positions(
        self, positions: list[Position], selected_id: str | None, client: DataSource, error: str
    ) -> str | None:
        table = self.query_one(DataTable)
        previous_id = selected_id
        same_rows = [row.value for row in table.rows] == [p.id for p in positions]
        with table.prevent(DataTable.RowHighlighted):
            if not same_rows:
                table.clear()
            for row_index, p in enumerate(positions):
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
            index = next((i for i, p in enumerate(positions) if p.id == previous_id), 0)
            if positions:
                selected_id = positions[index].id
                if not same_rows:
                    table.move_cursor(row=index)
            else:
                selected_id = None
        table.display = bool(positions)
        empty = self.query_one("#empty", Static)
        empty.display = not positions
        if error:
            empty.update(_("Positions unavailable · press r to retry"))
        elif not client.authenticated:
            empty.update(_(client.public_hint))
        else:
            empty.update(
                _(
                    "No open positions · {symbol} is the default chart",
                    symbol=client.default_symbol.replace("_", " / "),
                )
            )
        summary = Text(
            " " + _("{count} positions  ·  ↑ ↓ / j k select chart", count=len(positions)),
            style=MUTED,
        )
        if positions:
            totals: dict[str, Decimal] = {}
            for p in positions:
                if p.pnl is not None:
                    totals[p.settlement] = totals.get(p.settlement, Decimal(0)) + p.pnl
            for settlement, pnl in totals.items():
                summary.append("  ·  " + _("uPnL") + " ")
                summary.append_text(signed(pnl, " " + settlement))
        self.query_one("#positions-summary", Static).update(summary)
        return selected_id

    def update_layout(
        self,
        client: DataSource,
        count: int,
        error: str,
        initial_selection: bool,
        override: bool | None,
        terminal_height: int,
        on_hide: Callable[[], object],
    ) -> None:
        pane = self
        table = self.query_one(DataTable)
        visible = client.authenticated and (
            override if override is not None else bool(count or error or not initial_selection)
        )
        max_height = min(12, max(5, terminal_height // 4))
        # A horizontal scrollbar also consumes a table row on narrow terminals.
        scrollbar_height = table.scrollbar_size_horizontal if count else 0
        pane.styles.height = min(count + 4 + scrollbar_height if count else 4, max_height)
        # Move focus before hiding its parent so Textual doesn't select another control.
        if not visible and self.app.focused is table:
            on_hide()
        pane.display = visible
