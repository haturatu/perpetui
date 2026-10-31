"""In-app keyboard reference overlay."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .i18n import _


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,q,question_mark", "dismiss", "Close", show=False),
    ]
    CSS = """
    HelpScreen { align: center middle; background: $background 60%; }
    #help-box { width: 76; height: auto; max-height: 90%; padding: 1 2;
        border: round #7aa2ff; background: #101a2a; }
    #help-title { height: 1; color: #e4edf8; text-style: bold; }
    #help-body { height: auto; color: #aebfd8; }
    """

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(_("KEYBOARD SHORTCUTS"), id="help-title")
            width = max((len(keys) for keys, _ in self.rows), default=0)
            body = "\n".join(f"{keys:<{width}}  {description}" for keys, description in self.rows)
            yield Static(body, id="help-body")

    def action_dismiss(self) -> None:
        self.dismiss()
