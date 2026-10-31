"""Symbol input with a keyboard-accessible suggestion popup."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from perpetui.i18n import _


def matching_symbols(query: str, symbols: list[str], limit: int = 8) -> list[str]:
    prefix = query.strip().upper().replace("/", "").replace("-", "").replace("_", "")
    if not prefix:
        return []

    def rank(symbol: str) -> tuple[bool, bool, bool, str]:
        base, _, quote = symbol.partition("_")
        return (
            base.upper() != prefix,
            not symbol.upper().replace("_", "").startswith(prefix),
            quote != "USDT",
            symbol,
        )

    return sorted(
        {symbol for symbol in symbols if prefix in symbol.upper().replace("_", "")},
        key=rank,
    )[:limit]


class Suggestions(OptionList):
    can_focus = False


class SymbolSearch(Vertical):
    DEFAULT_CSS = """
    SymbolSearch {
        width: 24;
        height: 3;
        Input { width: 1fr; }
        Suggestions {
            display: none;
            width: 1fr;
            height: auto;
            max-height: 10;
            overlay: screen;
            constrain: none inside;
            border: tall #7aa2ff;
            background: #101a2a;
            color: #e4edf8;
            padding: 0;
            & > .option-list--option { padding: 0 1; }
            & > .option-list--option-highlighted {
                color: #e4edf8;
                background: #192d49;
            }
            & > .option-list--option-disabled { color: #788ba9; }
        }
    }
    """
    BINDINGS = [
        Binding("up", "move(-1)", "Previous symbol", show=False),
        Binding("down", "move(1)", "Next symbol", show=False),
        Binding("escape", "cancel", "Close search", show=False),
    ]

    class Chosen(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class Cancelled(Message):
        pass

    def __init__(self) -> None:
        super().__init__(id="symbol-search")
        self.symbols: list[str] = []
        self.loading = True
        self.unavailable = False

    def compose(self) -> ComposeResult:
        yield Input(placeholder=_("Symbol · e.g. BTC"), id="symbol-input")
        yield Suggestions(id="symbol-suggestions")

    def set_symbols(self, symbols: list[str], *, unavailable: bool = False) -> None:
        self.symbols = symbols
        self.loading = False
        self.unavailable = unavailable
        self._update_suggestions()

    def reset(self) -> None:
        self.symbols = []
        self.loading, self.unavailable = True, False
        self.query_one(Input).value = ""
        self.query_one(Suggestions).clear_options()
        self.query_one(Suggestions).display = False

    def _update_suggestions(self) -> None:
        field = self.query_one(Input)
        popup = self.query_one(Suggestions)
        if not field.has_focus or not field.value.strip():
            popup.display = False
            return
        matches = matching_symbols(field.value, self.symbols)
        if matches:
            popup.set_options(
                Option(Text(symbol.replace("_", " / ")), id=symbol) for symbol in matches
            )
            popup.highlighted = 0
        else:
            message = (
                "Loading symbols…"
                if self.loading
                else "Suggestions unavailable"
                if self.unavailable
                else "No matching symbols"
            )
            popup.set_options([Option(_(message), disabled=True)])
        popup.display = True

    def on_descendant_focus(self) -> None:
        self._update_suggestions()

    @on(Input.Changed)
    def input_changed(self, event: Input.Changed) -> None:
        self._update_suggestions()

    @on(Input.Blurred)
    def input_blurred(self) -> None:
        self.query_one(Suggestions).display = False

    def action_move(self, direction: int) -> None:
        popup = self.query_one(Suggestions)
        if not popup.display:
            self._update_suggestions()
            return
        if popup.highlighted is not None:
            popup.highlighted = (popup.highlighted + direction) % popup.option_count
            popup.scroll_to_highlight()

    def action_cancel(self) -> None:
        self.query_one(Suggestions).display = False
        self.post_message(self.Cancelled())

    @on(Input.Submitted)
    def input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        popup = self.query_one(Suggestions)
        option = popup.highlighted_option if popup.display else None
        value = option.id if option is not None and not option.disabled else event.value
        self._choose(value or event.value)

    @on(OptionList.OptionSelected)
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            self._choose(event.option.id)

    def _choose(self, value: str) -> None:
        self.query_one(Suggestions).display = False
        self.post_message(self.Chosen(value))
