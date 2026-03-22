"""Experiment policy compiler helpers.

Canonical compiler behavior for environment-scoped policy resolution used by
preview and launch paths.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from app.experiment_policy import hash_canonical_json, validate_policy_shape


EXPERIMENT_COMPILER_VERSION = "1.0"


class ExperimentCompilerError(ValueError):
    """Structured compiler error with stable taxonomy code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "invalid_schema").strip() or "invalid_schema"
        self.message = str(message or "invalid experiment compiler input").strip()

    def __str__(self) -> str:  # pragma: no cover
        return self.message

def _as_dict(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ExperimentCompilerError("invalid_schema", f"{field_name} must be an object")
    return dict(value)


def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged.get(key) or {}, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def extract_group_overrides(environment_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract group overrides from environment config for compiler precedence.

    Accepted inputs:
    1) `environment_config.group_overrides`
    2) legacy `environment_config.experiment_policy.core.group_overrides`
    """
    if not isinstance(environment_config, dict):
        return {}

    if "group_overrides" in environment_config:
        return _as_dict(
            environment_config.get("group_overrides"),
            field_name="environment_config.group_overrides",
        )

    legacy_policy = environment_config.get("experiment_policy")
    if isinstance(legacy_policy, dict):
        legacy_core = legacy_policy.get("core")
        if isinstance(legacy_core, dict) and "group_overrides" in legacy_core:
            return _as_dict(
                legacy_core.get("group_overrides"),
                field_name="environment_config.experiment_policy.core.group_overrides",
            )

    return {}


def validate_policy_compile_inputs(environment_config: Optional[Dict[str, Any]]) -> None:
    """Reject disallowed inline policy overrides.

    Callers may only provide group overrides. Any other inline
    experiment policy input is rejected fail-closed.
    """
    if not isinstance(environment_config, dict):
        return

    raw_policy = environment_config.get("experiment_policy")
    if raw_policy is None:
        return

    if not isinstance(raw_policy, dict):
        raise ExperimentCompilerError(
            "invalid_schema",
            "environment_config.experiment_policy must be an object",
        )

    raw_core = raw_policy.get("core")
    if raw_core is None:
        raw_core = {}
    if not isinstance(raw_core, dict):
        raise ExperimentCompilerError(
            "invalid_schema",
            "environment_config.experiment_policy.core must be an object",
        )

    disallowed_top_level = sorted(
        key for key in raw_policy.keys() if key not in {"core", "policy_version", "env"}
    )
    if disallowed_top_level:
        raise ExperimentCompilerError(
            "unsupported_feature",
            "Disallowed inline policy fields: " + ", ".join(disallowed_top_level),
        )

    disallowed_core = sorted(
        key
        for key in raw_core.keys()
        if key not in {"group_overrides"}
    )
    if disallowed_core:
        raise ExperimentCompilerError(
            "unsupported_feature",
            "Only core.group_overrides is accepted; disallowed keys: "
            + ", ".join(disallowed_core),
        )

    if raw_policy.get("env"):
        raise ExperimentCompilerError(
            "unsupported_feature",
            "Policy compile does not allow inline environment policy overrides",
        )


def _enforce_no_agent_overrides(policy_json: Dict[str, Any]) -> None:
    core = policy_json.get("core") if isinstance(policy_json.get("core"), dict) else {}
    if not core:
        return
    agent_overrides = core.get("agent_overrides")
    if not agent_overrides:
        return
    raise ExperimentCompilerError(
        "unsupported_feature",
        "Per-agent overrides are deferred and not supported in this module",
    )


def compile_policy_with_group_overrides(
    *,
    base_policy_json: Dict[str, Any],
    group_overrides: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compile policy using precedence: environment defaults -> base -> group."""
    normalized_base = validate_policy_shape(base_policy_json)
    _enforce_no_agent_overrides(normalized_base)

    compiled_policy = deepcopy(normalized_base)
    compiled_core = dict(compiled_policy.get("core") or {})
    if group_overrides:
        runtime_group_overrides = _as_dict(
            group_overrides,
            field_name="group_overrides",
        )
        base_group_overrides = _as_dict(
            compiled_core.get("group_overrides"),
            field_name="compiled_policy.core.group_overrides",
        )
        compiled_core["group_overrides"] = _deep_merge_dict(
            base_group_overrides,
            runtime_group_overrides,
        )
    compiled_policy["core"] = compiled_core

    normalized_compiled = validate_policy_shape(compiled_policy)
    _enforce_no_agent_overrides(normalized_compiled)
    return normalized_compiled


def compute_compiled_experiment_hash(
    *,
    environment_ref: str,
    policy_hash: Optional[str],
    manifest_hash: Optional[str],
    compile_source: str,
) -> str:
    """Compute deterministic compiler identity hash."""
    payload = {
        "environment_ref": str(environment_ref or "").strip(),
        "policy_hash": str(policy_hash or "").strip() or None,
        "manifest_hash": str(manifest_hash or "").strip() or None,
        "compile_source": str(compile_source or "").strip() or "inline_legacy",
        "compiler_version": EXPERIMENT_COMPILER_VERSION,
        "algorithm": "sha256",
    }
    return hash_canonical_json(payload)
