"""Bounded structured snapshots for HTTP response telemetry."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|token|authorization|secret|password|cookie|session|credential|bearer)",
    flags=re.IGNORECASE,
)
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", flags=re.IGNORECASE),
    re.compile(
        r"(?i)(api[_-]?key|token|authorization|secret|password|cookie|session|credential)\s*[:=]\s*[^\s,;]+"
    ),
)
SNAPSHOT_SCHEMA = "mase.http_response_snapshot.v1"
TRUNCATED_MARKER = "__mase_truncated__"
SUMMARY_MARKER = "__mase_summary__"


def _sanitize_text(text: str) -> Tuple[str, bool]:
    value = str(text or "")
    redacted = False
    for pattern in SENSITIVE_TEXT_PATTERNS:
        updated = pattern.sub(lambda _match: "***", value)
        if updated != value:
            redacted = True
            value = updated
    return value, redacted


def _summarize_container(value: Any, reason: str) -> Dict[str, Any]:
    size = len(value) if isinstance(value, (dict, list)) else None
    kind = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else type(value).__name__
    summary: Dict[str, Any] = {"type": kind, "reason": reason}
    if size is not None:
        summary["size"] = size
    return {SUMMARY_MARKER: summary}


def _snapshot_value(
    value: Any,
    *,
    depth: int,
    key_hint: Optional[str],
    stats: Dict[str, Any],
    limits: Dict[str, int],
) -> Any:
    stats["nodes"] += 1
    if stats["nodes"] > limits["max_total_nodes"]:
        stats["truncated"] = True
        return _summarize_container(value, "max_total_nodes")

    if key_hint and SENSITIVE_KEY_PATTERN.search(key_hint):
        stats["redacted"] = True
        return "***"

    if isinstance(value, str):
        text, redacted = _sanitize_text(value)
        if redacted:
            stats["redacted"] = True
        if len(text) > limits["max_string_chars"]:
            stats["truncated"] = True
            return text[: limits["max_string_chars"]] + "..."
        return text

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    if depth >= limits["max_depth"]:
        stats["truncated"] = True
        return _summarize_container(value, "max_depth")

    if isinstance(value, dict):
        snapshot: Dict[str, Any] = {}
        items = list(value.items())
        for index, (raw_key, raw_value) in enumerate(items):
            if index >= limits["max_dict_keys"]:
                stats["truncated"] = True
                snapshot[TRUNCATED_MARKER] = {
                    "reason": "max_dict_keys",
                    "remaining": len(items) - limits["max_dict_keys"],
                }
                break
            key = str(raw_key)
            snapshot[key] = _snapshot_value(
                raw_value,
                depth=depth + 1,
                key_hint=key,
                stats=stats,
                limits=limits,
            )
        return snapshot

    if isinstance(value, list):
        snapshot = []
        for index, item in enumerate(value):
            if index >= limits["max_list_items"]:
                stats["truncated"] = True
                snapshot.append(
                    {
                        TRUNCATED_MARKER: {
                            "reason": "max_list_items",
                            "remaining": len(value) - limits["max_list_items"],
                        }
                    }
                )
                break
            snapshot.append(
                _snapshot_value(
                    item,
                    depth=depth + 1,
                    key_hint=None,
                    stats=stats,
                    limits=limits,
                )
            )
        return snapshot

    text, redacted = _sanitize_text(str(value))
    if redacted:
        stats["redacted"] = True
    if len(text) > limits["max_string_chars"]:
        stats["truncated"] = True
        return text[: limits["max_string_chars"]] + "..."
    return text


def build_response_snapshot(
    body: Any,
    *,
    enabled: bool,
    max_depth: int,
    max_dict_keys: int,
    max_list_items: int,
    max_string_chars: int,
    max_total_nodes: int,
) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Return a bounded structured snapshot for JSON-like HTTP bodies."""
    if not enabled or not isinstance(body, (dict, list)):
        return None, None

    limits = {
        "max_depth": max(1, int(max_depth)),
        "max_dict_keys": max(1, int(max_dict_keys)),
        "max_list_items": max(1, int(max_list_items)),
        "max_string_chars": max(16, int(max_string_chars)),
        "max_total_nodes": max(32, int(max_total_nodes)),
    }
    stats: Dict[str, Any] = {"nodes": 0, "truncated": False, "redacted": False}
    snapshot = _snapshot_value(
        body,
        depth=0,
        key_hint=None,
        stats=stats,
        limits=limits,
    )

    meta = {
        "schema": SNAPSHOT_SCHEMA,
        "body_type": "object" if isinstance(body, dict) else "array",
        "truncated": bool(stats["truncated"]),
        "redacted": bool(stats["redacted"]),
        "nodes_captured": int(stats["nodes"]),
        "limits": limits,
        "top_level_items_original": len(body),
        "top_level_items_captured": len(snapshot) if isinstance(snapshot, (dict, list)) else None,
    }
    return snapshot, meta
