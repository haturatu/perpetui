"""Notify the workspace when the chart area's actual dimensions change."""

from textual.containers import Grid
from textual.message import Message


class AdaptiveGrid(Grid):
    class Resized(Message):
        """The available chart area changed, including after positions updates."""

    def on_resize(self) -> None:
        self.post_message(self.Resized())
