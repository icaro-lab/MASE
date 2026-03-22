"""Neutral run configuration helpers for the public runtime/environment/run surface."""

from __future__ import annotations

import hashlib
import json
import re
from math import ceil
from typing import Any


def coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort integer coercion for external status payloads."""
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        return int(float(str(value).strip()))
    except Exception:
        return default


def coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_duration_literal_seconds(value: Any) -> float | None:
    """Parse duration values like 30, '30s', '2m', '1h' into seconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text or text in {"none", "null"}:
        return None

    numeric = coerce_optional_float(text)
    if numeric is not None:
        return float(numeric)

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])", text)
    if not match:
        return None

    amount = float(match.group(1))
    unit = match.group(2)
    multiplier = {
        "s": 1.0,
        "m": 60.0,
        "h": 3600.0,
        "d": 86400.0,
    }[unit]
    return amount * multiplier


def _normalize_runtime_limit_minutes(value: Any) -> int | None:
    minutes = coerce_optional_float(value)
    if minutes is None or minutes <= 0:
        return None
    return int(ceil(minutes))


def _normalize_max_ticks(value: Any) -> int | None:
    ticks = coerce_optional_float(value)
    if ticks is None or ticks <= 0:
        return None
    return int(ceil(ticks))


def resolve_run_runtime_limit(environment_config: dict[str, Any] | None) -> tuple[int | None, str]:
    """Resolve canonical runtime limit in minutes from environment config."""
    if not isinstance(environment_config, dict):
        return None, "none"

    if "runtime_limit_minutes" in environment_config:
        return _normalize_runtime_limit_minutes(environment_config.get("runtime_limit_minutes")), "runtime_limit_minutes"

    if "duration_minutes" in environment_config:
        return _normalize_runtime_limit_minutes(environment_config.get("duration_minutes")), "duration_minutes"

    phases = environment_config.get("phases")
    if isinstance(phases, dict):
        running = phases.get("running")
        if isinstance(running, dict) and "duration" in running:
            running_seconds = _parse_duration_literal_seconds(running.get("duration"))
            if running_seconds is None or running_seconds <= 0:
                return None, "phases.running.duration"
            return int(ceil(running_seconds / 60.0)), "phases.running.duration"

    return None, "none"


def resolve_run_max_ticks(environment_config: dict[str, Any] | None) -> tuple[int | None, str]:
    """Resolve optional max tick budget from environment config."""
    if not isinstance(environment_config, dict):
        return None, "none"

    if "max_ticks" in environment_config:
        return _normalize_max_ticks(environment_config.get("max_ticks")), "max_ticks"

    return None, "none"


def resolve_run_max_heartbeats_per_agent(environment_config: dict[str, Any] | None) -> tuple[int | None, str]:
    """Resolve optional per-agent heartbeat cap from environment config."""
    if not isinstance(environment_config, dict):
        return None, "none"

    if "max_heartbeats_per_agent" in environment_config:
        return _normalize_max_ticks(environment_config.get("max_heartbeats_per_agent")), "max_heartbeats_per_agent"

    return None, "none"


def compute_resolved_bundle_hash(
    *,
    environment_ref: str,
    environment_config: dict[str, Any] | None,
) -> str:
    """Compute a stable bundle hash when the client does not provide one."""
    payload = {
        "environment_ref": environment_ref,
        "environment_config": environment_config or {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return f"sha256:{digest}"
