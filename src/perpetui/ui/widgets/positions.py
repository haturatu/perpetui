"""Position-table rendering and sizing, with selection kept stable on updates."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Select, Static

from perpetui.domain.models import Position
from perpetui.exchanges.base import DataSource
from perpetui.i18n import _
from perpetui.services.portfolio import PortfolioPosition, PortfolioService
from perpetui.ui.formatting import price, signed
from perpetui.ui.theme import ACCENT, DOWN, MUTED, UP


class PositionsPane(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(id="portfolio-filters"):
            yield Select(
                [("All accounts", "all")],
                value="all",
                allow_blank=False,
                compact=True,
                id="position-scope",
            )
            yield Select(
                [
                    ("Original", "none"),
                    ("Exchange", "exchange"),
                    ("Symbol", "symbol"),
                    ("Exposure", "exposure"),
                ],
                value="none",
                allow_blank=False,
                compact=True,
                id="position-group",
            )
        yield Static("", id="positions-summary")
        yield DataTable(id="positions", cursor_type="row", zebra_stripes=True)
        yield DataTable(id="exposures", cursor_type="none", zebra_stripes=True)
        yield Static(_("Loading open positions…"), id="empty")

    def configure_portfolio(
        self, options: list[tuple[str, str]], scope: str, group_by: str
    ) -> None:
        select = self.query_one("#position-scope", Select)
        with select.prevent(Select.Changed):
            select.set_options(options)
            select.value = scope
        group = self.query_one("#position-group", Select)
        with group.prevent(Select.Changed):
            group.value = group_by

    def setup(self, client: DataSource, *, portfolio: bool = False) -> None:
        self.query_one("#portfolio-filters").display = portfolio
        exposure_table = self.query_one("#exposures", DataTable)
        exposure_table.display = False
        exposure_table.clear(columns=True)
        exposure_table.add_columns("Asset", "Long", "Short", "Net", "Gross", "Long %")
        table = self.query_one("#positions", DataTable)
        with table.prevent(DataTable.RowHighlighted):
            table.clear(columns=True)
        table.add_columns(
            *map(
                _,
                (
                    *(("Source",) if portfolio else ()),
                    "Symbol",
                    "Side",
                    "Qty" if portfolio else client.quantity_label,
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
        self,
        positions: list[Position | PortfolioPosition],
        selected_id: str | None,
        client: DataSource,
        error: str,
        *,
        portfolio: PortfolioService | None = None,
    ) -> str | None:
        table = self.query_one("#positions", DataTable)
        previous_id = selected_id
        same_rows = [row.value for row in table.rows] == [p.id for p in positions]
        with table.prevent(DataTable.RowHighlighted):
            if not same_rows:
                table.clear()
            for row_index, p in enumerate(positions):
                quantity = (
                    f"{p.contracts:,.0f}" if p.contracts == int(p.contracts) else str(p.contracts)
                )
                if portfolio is not None:
                    quantity += " ctr" if p.account.exchange == "mexc" else " base"
                cells = (
                    *(
                        (
                            Text(
                                f"{p.account.exchange} / {p.account.label or p.account.id}",
                                style=MUTED,
                            ),
                        )
                        if portfolio is not None
                        else ()
                    ),
                    Text(p.symbol.replace("_", "/"), style=ACCENT),
                    Text(_(p.side), style=UP if p.side == "LONG" else DOWN),
                    quantity,
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
        elif portfolio is not None:
            empty.update(_("No open positions in selected accounts"))
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
            if portfolio is not None:
                converted = portfolio.usd_pnl(positions)
                if converted is not None:
                    summary.append("  ·  USD≈ ")
                    summary.append_text(signed(converted))
                exposures = portfolio.exposures(positions)
                if len(exposures) == 1:
                    exposure = next(iter(exposures.values()))
                    summary.append(
                        f"  ·  {exposure.instrument} net {exposure.net_size:+} "
                        f"/ gross {exposure.gross_size}"
                    )
        risk_mode = (
            portfolio is not None and getattr(self.app, "_portfolio_group", "none") == "exposure"
        )
        exposure_table = self.query_one("#exposures", DataTable)
        exposure_table.display = False
        if risk_mode:
            exposures = portfolio.exposures(positions)
            exposure_table.clear()
            for exposure in sorted(exposures.values(), key=lambda item: item.instrument):
                ratio = (
                    exposure.long_size / exposure.gross_size * Decimal(100)
                    if exposure.gross_size
                    else Decimal(0)
                )
                exposure_table.add_row(
                    exposure.instrument,
                    str(exposure.long_size),
                    str(exposure.short_size),
                    f"{exposure.net_size:+}",
                    str(exposure.gross_size),
                    f"{ratio:.1f}%",
                )
            table.display = False
            exposure_table.display = bool(exposures)
            if not exposures:
                empty.display = True
                empty.update(_("No comparable linear contract positions"))
            margins = portfolio.margin_by_venue(positions)
            for (exchange, currency), margin in sorted(margins.items()):
                summary.append(f"  ·  {exchange} margin {price(margin)} {currency}")
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
        *,
        portfolio_enabled: bool = False,
    ) -> None:
        pane = self
        table = self.query_one("#positions", DataTable)
        visible = (client.authenticated or portfolio_enabled) and (
            override if override is not None else bool(count or error or not initial_selection)
        )
        extra = 3 if portfolio_enabled else 0
        max_height = min(15 if portfolio_enabled else 12, max(5 + extra, terminal_height // 4))
        # A horizontal scrollbar also consumes a table row on narrow terminals.
        scrollbar_height = table.scrollbar_size_horizontal if count else 0
        pane.styles.height = min((count + 4 + scrollbar_height if count else 4) + extra, max_height)
        # Move focus before hiding its parent so Textual doesn't select another control.
        if not visible and self.app.focused is table:
            on_hide()
        pane.display = visible
