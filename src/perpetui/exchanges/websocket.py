from __future__ import annotations

import gzip
import json
from decimal import Decimal
from typing import Any


class HistoryGap(ValueError):
    """A stream skipped one or more candle intervals."""


def decode_message(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes) and raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    message = json.loads(raw, parse_float=Decimal)
    if not isinstance(message, dict):
        raise ValueError("Invalid WebSocket message")
    return message
