"""Workspace-only selection and presentation choices, separate from market data."""

from dataclasses import dataclass

from perpetui.ui.layout import LayoutMode


@dataclass
class WorkspaceState:
    active_chart_id: int = 1
    chart_count: int = 1
    desired_chart_count: int = 1
    layout_busy: bool = False
    layout_mode: LayoutMode = "auto"
    maximized: bool = False
    all_timeframes: bool = False
    cursor_sync: bool = False
    positions_override: bool | None = None
