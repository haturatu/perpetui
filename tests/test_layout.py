import pytest

from perpetui.ui.layout import LAYOUT_MODES, choose_layout


@pytest.mark.parametrize(
    "count,width,height,expected",
    [
        (1, 120, 40, (1,)),
        (2, 120, 40, (2,)),
        (3, 120, 40, (1, 2)),
        (3, 160, 40, (1, 2)),
        (3, 220, 40, (3,)),
        (4, 120, 40, (2, 2)),
        (4, 160, 40, (2, 2)),
        (4, 220, 40, (4,)),
        (4, 240, 40, (4,)),
        (5, 120, 40, (3, 2)),
        (3, 48, 90, (1, 1, 1)),
    ],
)
def test_layout_uses_chart_count_and_available_area(count, width, height, expected):
    assert choose_layout(count, width, height) == expected


@pytest.mark.parametrize("count", range(1, 10))
@pytest.mark.parametrize("mode", LAYOUT_MODES)
def test_layouts_keep_every_chart_and_manual_modes_ignore_size(count, mode):
    for width, height in ((0, 0), (1, 1), (48, 90), (120, 40), (240, 20)):
        rows = choose_layout(count, width, height, mode)
        assert sum(rows) == count and all(row > 0 for row in rows)
        if mode != "auto":
            assert rows == choose_layout(count, 120, 40, mode)
    if mode == "horizontal":
        assert rows == (count,)
    elif mode == "vertical":
        assert rows == (1,) * count
    elif mode == "grid":
        assert max(rows) - min(rows) <= 1


@pytest.mark.parametrize("count", [0, 10])
def test_layout_rejects_unsupported_chart_counts(count):
    with pytest.raises(ValueError, match="Chart count"):
        choose_layout(count, 120, 40)


def test_layout_rejects_unknown_preset():
    with pytest.raises(ValueError, match="Unknown layout"):
        choose_layout(3, 120, 40, "unknown")
