"""Heartbeat contract utilities shared across controller runtime paths."""

from __future__ import annotations

import math
import re
from typing import Any, Dict

_DURATION_RE = re.compile(r"^\s*(\d+)\s*(ms|s|m|h)?\s*$", re.IGNORECASE)


def parse_duration_seconds(value: Any, default_seconds: int) -> int:
    """Parse duration strings like 30s/2m/1h with safe fallback."""
    if value is None:
        return max(default_seconds, 1)
    text = str(value).strip()
    if not text:
        return max(default_seconds, 1)

    match = _DURATION_RE.match(text)
    if not match:
        return max(default_seconds, 1)

    amount = int(match.group(1))
    unit = (match.group(2) or "s").lower()
    if unit == "ms":
        seconds = max(1, math.ceil(amount / 1000))
    elif unit == "s":
        seconds = amount
    elif unit == "m":
        seconds = amount * 60
    elif unit == "h":
        seconds = amount * 3600
    else:
        seconds = default_seconds
    return max(seconds, 1)


def _coerce_positive_int(value: Any, default_value: int) -> int:
    """Parse positive integer-ish values with safe fallback."""
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return max(default_value, 1)
    if parsed <= 0:
        return max(default_value, 1)
    return parsed


def resolve_scheduler_heartbeat_timeout(heartbeat_config: Dict[str, Any]) -> str:
    """Ensure scheduler timeout is >= max_tick_runtime_ms + 30s margin."""
    configured_timeout = heartbeat_config.get("timeout", "30s")
    configured_seconds = parse_duration_seconds(configured_timeout, 30)
    max_tick_runtime_ms = _coerce_positive_int(
        heartbeat_config.get("max_tick_runtime_ms"),
        90000,
    )
    required_seconds = max(1, math.ceil(max_tick_runtime_ms / 1000) + 30)
    return f"{max(configured_seconds, required_seconds)}s"
