"""Heartbeat runtime policy/gating and bounded turn orchestration."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .openclaw_core.loop import (
    DEFAULT_HEARTBEAT_PROMPT,
    INTERACTION_MODE,
    PROMPT_CONTRACT_VERSION,
    RoundZeroFallback,
    StopReason,
    TurnOrchestrator,
    TurnResult,
    format_observation_message,
    normalize_observations,
)

MEMORY_MODE_STATELESS = "stateless"
MEMORY_MODE_LAST_N_TURNS = "last_n_turns"
MEMORY_MODE_SESSION_TRANSCRIPT = "session_transcript"
SUPPORTED_MEMORY_MODES = {
    MEMORY_MODE_STATELESS,
    MEMORY_MODE_LAST_N_TURNS,
    MEMORY_MODE_SESSION_TRANSCRIPT,
}
SUPPORTED_AGENT_CORES = {
    "legacy_mase",
    "openclaw_py_core",
    "simplified_social_core",
    "openclaw_py_minimal_core",
}


def _coerce_nonnegative_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _coerce_positive_int(value: Any, default: int, minimum: int = 1) -> int:
    parsed = _coerce_nonnegative_int(value, default)
    if parsed < minimum:
        return default
    return parsed


def _coerce_optional_positive_int(value: Any, default: Optional[int], minimum: int = 1) -> Optional[int]:
    if value is None:
        return default
    parsed = _coerce_nonnegative_int(value, 0)
    if parsed == 0:
        return None
    if parsed < minimum:
        return default
    return parsed


def _coerce_nonnegative_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_string(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _coerce_memory_mode(value: Any, default: str = MEMORY_MODE_STATELESS) -> str:
    mode = _coerce_string(value, default).lower()
    if mode not in SUPPORTED_MEMORY_MODES:
        return default
    return mode


def _coerce_agent_core(value: Any, default: str = "legacy_mase") -> str:
    core = _coerce_string(value, default).lower()
    if core not in SUPPORTED_AGENT_CORES:
        return default
    return core


def _sanitize_json_value(value: Any) -> Any:
    """Strip common credential keys recursively from JSON-like objects."""
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "authorization", "api_key", "secret")):
                redacted[str(key)] = "***"
                continue
            redacted[str(key)] = _sanitize_json_value(nested)
        return redacted
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    return value


def _sanitize_preview_text(text: str) -> str:
    preview = str(text or "")
    preview = re.sub(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer ***", preview, flags=re.IGNORECASE)
    preview = re.sub(r"(?i)(api[_-]?key|token|authorization)\s*[:=]\s*[^\s,;]+", r"\1=***", preview)
    return preview


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return str(value)


def summarize_action(action: Action) -> Dict[str, Any]:
    """Stable action summary for telemetry and round details."""
    data: Dict[str, Any] = {}
    try:
        if hasattr(action, "model_dump"):
            data = action.model_dump()
        elif hasattr(action, "dict"):
            data = action.dict()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    keep = ("action", "action_name", "description", "method", "url", "operation", "path")
    summary: Dict[str, Any] = {}
    for key in keep:
        value = data.get(key)
        if value is None or value == "":
            continue
        summary[key] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return summary


@dataclass(frozen=True)
class HeartbeatPolicy:
    """Resolved per-agent heartbeat policy."""

    enabled: bool = True
    every: str = "5m"
    model: Optional[str] = None
    prompt: str = DEFAULT_HEARTBEAT_PROMPT
    active_hours: Optional[Dict[str, Any]] = None
    skip_if_heartbeat_empty: bool = False
    max_prompt_chars: int = 32000
    max_skill_chars: int = 8000
    max_model_calls_per_tick: Optional[int] = 3
    max_actions_per_round: Optional[int] = 3
    max_actions_per_tick: Optional[int] = 8
    max_tick_runtime_ms: Optional[int] = 90000
    max_observation_chars_per_round: int = 2000
    heartbeat_tail_chars: int = 6000
    single_skill_mode: bool = True
    memory_mode: str = MEMORY_MODE_STATELESS
    memory_turn_window: int = 0
    memory_max_chars: int = 4000
    agent_core: str = "legacy_mase"


@dataclass
class PolicyResolution:
    """Policy resolution result with metadata."""

    policy: HeartbeatPolicy
    source: str
    config_path: str
    config_hash: Optional[str]
    raw_policy: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GateDecision:
    """Result of heartbeat pre-loop gating checks."""

    should_skip: bool
    reason: Optional[str] = None
    summary: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)



def load_agent_config(agent_root: Path) -> Dict[str, Any]:
    """Load agent config.json; return empty dict on failure."""
    config_path = agent_root / "config.json"
    if not config_path.exists() or not config_path.is_file():
        return {}
    try:
        parsed = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_heartbeat_policy(agent_root: Path) -> PolicyResolution:
    """Resolve heartbeat policy from agent config + launcher defaults."""
    defaults = HeartbeatPolicy()
    config_path = agent_root / "config.json"
    raw_config = load_agent_config(agent_root)
    source = "defaults"
    raw_policy: Dict[str, Any] = {}
    config_hash: Optional[str] = None

    if config_path.exists() and config_path.is_file():
        try:
            config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
        except Exception:
            config_hash = None

    if isinstance(raw_config.get("heartbeat"), dict):
        raw_policy = dict(raw_config["heartbeat"])
        source = "agent_config"
    elif raw_config:
        source = "defaults_no_heartbeat_block"

    # Backward compatibility: legacy heartbeat schema fields.
    if "every" not in raw_policy and raw_policy.get("interval_seconds") is not None:
        raw_policy["every"] = f"{_coerce_positive_int(raw_policy.get('interval_seconds'), 60)}s"
    if "model" not in raw_policy:
        model_block = raw_config.get("model")
        if isinstance(model_block, dict) and model_block.get("model"):
            raw_policy["model"] = model_block.get("model")
    runtime_block = raw_config.get("runtime")
    if "agent_core" not in raw_policy and isinstance(runtime_block, dict) and runtime_block.get("agent_core"):
        raw_policy["agent_core"] = runtime_block.get("agent_core")

    active_hours = raw_policy.get("active_hours")
    if not isinstance(active_hours, dict):
        active_hours = None

    policy = HeartbeatPolicy(
        enabled=_coerce_bool(raw_policy.get("enabled"), defaults.enabled),
        every=_coerce_string(raw_policy.get("every"), defaults.every),
        model=(
            _coerce_string(raw_policy.get("model"), "")
            if raw_policy.get("model") is not None
            else defaults.model
        )
        or None,
        prompt=_coerce_string(raw_policy.get("prompt"), defaults.prompt),
        active_hours=active_hours,
        skip_if_heartbeat_empty=_coerce_bool(
            raw_policy.get("skip_if_heartbeat_empty"),
            defaults.skip_if_heartbeat_empty,
        ),
        max_prompt_chars=_coerce_positive_int(raw_policy.get("max_prompt_chars"), defaults.max_prompt_chars),
        max_skill_chars=_coerce_positive_int(raw_policy.get("max_skill_chars"), defaults.max_skill_chars),
        max_model_calls_per_tick=_coerce_optional_positive_int(
            raw_policy.get("max_model_calls_per_tick"),
            defaults.max_model_calls_per_tick,
        ),
        max_actions_per_round=_coerce_optional_positive_int(
            raw_policy.get("max_actions_per_round"),
            defaults.max_actions_per_round,
        ),
        max_actions_per_tick=_coerce_optional_positive_int(
            raw_policy.get("max_actions_per_tick"),
            defaults.max_actions_per_tick,
        ),
        max_tick_runtime_ms=_coerce_optional_positive_int(
            raw_policy.get("max_tick_runtime_ms"),
            defaults.max_tick_runtime_ms,
        ),
        max_observation_chars_per_round=_coerce_positive_int(
            raw_policy.get("max_observation_chars_per_round"),
            defaults.max_observation_chars_per_round,
        ),
        heartbeat_tail_chars=_coerce_positive_int(
            raw_policy.get("heartbeat_tail_chars"),
            defaults.heartbeat_tail_chars,
        ),
        single_skill_mode=_coerce_bool(raw_policy.get("single_skill_mode"), defaults.single_skill_mode),
        memory_mode=_coerce_memory_mode(raw_policy.get("memory_mode"), defaults.memory_mode),
        memory_turn_window=_coerce_nonnegative_int(
            raw_policy.get("memory_turn_window"),
            defaults.memory_turn_window,
        ),
        memory_max_chars=_coerce_positive_int(
            raw_policy.get("memory_max_chars"),
            defaults.memory_max_chars,
        ),
        agent_core=_coerce_agent_core(raw_policy.get("agent_core"), defaults.agent_core),
    )

    return PolicyResolution(
        policy=policy,
        source=source,
        config_path=str(config_path),
        config_hash=config_hash,
        raw_policy=raw_policy,
    )


def _within_active_hours(active_hours: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    if not active_hours:
        return True
    start_raw = str(active_hours.get("start") or "").strip()
    end_raw = str(active_hours.get("end") or "").strip()
    if not start_raw or not end_raw:
        return True

    try:
        start_h, start_m = [int(part) for part in start_raw.split(":", 1)]
        end_h, end_m = [int(part) for part in end_raw.split(":", 1)]
    except Exception:
        return True

    reference = now or datetime.now(timezone.utc)
    minute = reference.hour * 60 + reference.minute
    start_minute = start_h * 60 + start_m
    end_minute = end_h * 60 + end_m

    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= minute < end_minute
    return minute >= start_minute or minute < end_minute


def evaluate_gating(
    policy: HeartbeatPolicy,
    *,
    heartbeat_content: Optional[str],
    scheduler_status: Optional[Dict[str, Any]] = None,
) -> GateDecision:
    """Evaluate whether heartbeat should be skipped before model execution."""
    if not policy.enabled:
        return GateDecision(
            should_skip=True,
            reason=StopReason.GATED_SKIP,
            summary="Heartbeat skipped: heartbeat policy disabled",
            metadata={"gate": "policy_disabled"},
        )

    if policy.skip_if_heartbeat_empty and not str(heartbeat_content or "").strip():
        return GateDecision(
            should_skip=True,
            reason=StopReason.GATED_SKIP,
            summary="Heartbeat skipped: HEARTBEAT.md is empty and policy requires skipping",
            metadata={"gate": "heartbeat_empty"},
        )

    if not _within_active_hours(policy.active_hours):
        return GateDecision(
            should_skip=True,
            reason=StopReason.GATED_SKIP,
            summary="Heartbeat skipped: outside configured active_hours",
            metadata={"gate": "active_hours"},
        )

    if isinstance(scheduler_status, dict):
        in_flight = _coerce_nonnegative_int(scheduler_status.get("in_flight_tasks"), 0)
        max_parallel = _coerce_positive_int(scheduler_status.get("max_parallel_agents"), 0, minimum=0)
        if max_parallel > 0 and in_flight > (max_parallel * 2):
            return GateDecision(
                should_skip=True,
                reason=StopReason.GATED_SKIP,
                summary="Heartbeat skipped: scheduler backpressure threshold exceeded",
                metadata={
                    "gate": "backpressure",
                    "in_flight_tasks": in_flight,
                    "max_parallel_agents": max_parallel,
                },
            )

    return GateDecision(should_skip=False)


def build_turn_memory_message(
    entries: List[str],
    *,
    max_chars: int,
) -> Optional[str]:
    """Build bounded user-role memory context from prior heartbeat log entries."""
    sanitized_entries = [str(entry or "").strip() for entry in entries if str(entry or "").strip()]
    if not sanitized_entries:
        return None

    lines: List[str] = [
        "Recent turn memory from prior heartbeats:",
        "",
    ]
    for idx, entry in enumerate(sanitized_entries, start=1):
        lines.append(f"[memory {idx}]")
        lines.append(entry)
        lines.append("")

    text = "\n".join(lines).strip()
    budget = _coerce_positive_int(max_chars, 4000)
    if len(text) > budget:
        tail = text[-budget:]
        text = f"[memory context truncated to last {budget} chars]\n{tail}"
    return text
