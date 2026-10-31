"""MEXC wire identifiers paired with common durations."""

from perpetui.domain.intervals import INTERVAL_SECONDS

MEXC_INTERVALS = {
    "1m": ("Min1", INTERVAL_SECONDS["1m"]),
    "5m": ("Min5", INTERVAL_SECONDS["5m"]),
    "15m": ("Min15", INTERVAL_SECONDS["15m"]),
    "30m": ("Min30", INTERVAL_SECONDS["30m"]),
    "1h": ("Min60", INTERVAL_SECONDS["1h"]),
    "4h": ("Hour4", INTERVAL_SECONDS["4h"]),
    "1d": ("Day1", INTERVAL_SECONDS["1d"]),
    "1w": ("Week1", INTERVAL_SECONDS["1w"]),
}
