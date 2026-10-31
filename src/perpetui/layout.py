"""Choose chart rows using terminal cells, independently of Textual widgets."""

from __future__ import annotations

from math import ceil, lcm, log, sqrt
from typing import Literal

LayoutMode = Literal["auto", "horizontal", "grid", "vertical"]
LAYOUT_MODES: tuple[LayoutMode, ...] = ("auto", "horizontal", "grid", "vertical")

# Each value is the number of charts in a row, from top to bottom.
LAYOUTS = {
    1: ((1,),),
    2: ((2,), (1, 1)),
    3: ((3,), (1, 2)),
    4: ((4,), (2, 2)),
    5: ((5,), (3, 2)),
    6: ((6,), (3, 3), (2, 2, 2)),
    7: ((7,), (4, 3), (3, 2, 2)),
    8: ((8,), (4, 4), (3, 3, 2)),
    9: ((9,), (5, 4), (3, 3, 3)),
}


def layout_score(rows: tuple[int, ...], width: int, height: int) -> float:
    """Penalize cramped charts and aspect ratios far from 2.7 terminal cells."""
    columns = lcm(*rows)
    row_height = max(1, height / len(rows))
    column_width = max(0, (width - columns + 1) / columns)
    total = 0.0
    for count in rows:
        span = columns // count
        pane_width = max(1, column_width * span + span - 1)
        penalty = abs(log(pane_width / row_height / 2.7))
        penalty += 2 * max(0, 48 - pane_width) / 48
        penalty += 2 * max(0, 12 - row_height) / 12
        total += count * penalty
    return total / sum(rows)


def choose_layout(
    count: int, width: int, height: int, mode: LayoutMode = "auto"
) -> tuple[int, ...]:
    """Return full rows for 1–9 charts; manual modes ignore the available size."""
    if count not in LAYOUTS:
        raise ValueError("Chart count must be between 1 and 9")
    if mode not in LAYOUT_MODES:
        raise ValueError(f"Unknown layout mode: {mode}")
    if mode == "horizontal":
        return (count,)
    if mode == "vertical":
        return (1,) * count
    if mode == "grid":
        if count == 3:
            return (1, 2)
        row_count = ceil(count / ceil(sqrt(count)))
        per_row, extra = divmod(count, row_count)
        return tuple(per_row + (index < extra) for index in range(row_count))
    # Include a stack for narrow, tall terminals as well as the usual candidates.
    candidates = (*LAYOUTS[count], (1,) * count)
    return min(candidates, key=lambda rows: layout_score(rows, max(1, width), max(1, height)))
