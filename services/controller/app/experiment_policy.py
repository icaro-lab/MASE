"""Experiment policy helpers.

Provides strict validation, canonicalization, and manifest-aware compatibility
checks for environment-scoped experiment policy envelopes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


EXPERIMENT_POLICY_TOP_LEVEL_KEYS = {"policy_version", "core", "env"}
EXPERIMENT_POLICY_CORE_KEYS = {
    "required_capabilities",
    "require_policy_handoff",
    "schedule_mode",
    "population_groups",
    "group_overrides",
    "agent_overrides",
}
EXPERIMENT_CAPABILITIES_MANIFEST_FILENAME = "experiment.capabilities.json"
DEFAULT_POLICY_VERSION = "1.0"
HASH_PREFIX = "sha256:"
POPULATION_GROUP_REQUIRED_KEYS = {"share", "runtime_id", "model_id"}
POPULATION_GROUP_OPTIONAL_KEYS = {"role_label"}
POPULATION_GROUP_ALLOWED_KEYS = POPULATION_GROUP_REQUIRED_KEYS | POPULATION_GROUP_OPTIONAL_KEYS
GROUP_OVERRIDE_ALLOWED_KEYS = POPULATION_GROUP_ALLOWED_KEYS
POPULATION_MIX_CAPABILITY = "population_mix"
POPULATION_SHARE_TOTAL = 1.0
POPULATION_SHARE_TOLERANCE = 1e-9


class ExperimentPolicyError(ValueError):
    """Structured policy validation error with stable taxonomy code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "invalid_schema").strip() or "invalid_schema"
        self.message = str(message or "invalid experiment policy").strip()

    def __str__(self) -> str:  # pragma: no cover - inherited behavior wrapper
        return self.message


def _as_dict(value: Any, *, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ExperimentPolicyError("invalid_schema", f"{field_name} must be an object")
    return dict(value)


def _as_list_of_strings(value: Any, *, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExperimentPolicyError("invalid_schema", f"{field_name} must be an array of strings")
    rows: List[str] = []
    for idx, item in enumerate(value):
        text = str(item or "").strip()
        if not text:
            raise ExperimentPolicyError(
                "invalid_schema",
                f"{field_name}[{idx}] must be a non-empty string",
            )
        rows.append(text)
    return rows


def _as_required_string(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ExperimentPolicyError("missing_required", f"{field_name} is required")
    return text


def _as_optional_string(value: Any, *, field_name: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_share(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ExperimentPolicyError("invalid_schema", f"{field_name} must be numeric and > 0")
    try:
        share = float(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentPolicyError("invalid_schema", f"{field_name} must be numeric and > 0") from exc
    if share <= 0:
        raise ExperimentPolicyError("invalid_schema", f"{field_name} must be > 0")
    return share


def _normalize_population_groups(
    value: Any,
) -> Dict[str, Dict[str, Any]]:
    groups = _as_dict(value, field_name="experiment_policy.core.population_groups")
    if not groups:
        raise ExperimentPolicyError(
            "missing_required",
            "experiment_policy.core.population_groups must be a non-empty object",
        )

    normalized: Dict[str, Dict[str, Any]] = {}
    total_share = 0.0
    for raw_group_name, raw_payload in groups.items():
        group_name = _as_required_string(
            raw_group_name,
            field_name="experiment_policy.core.population_groups.<group_name>",
        )
        if group_name in normalized:
            raise ExperimentPolicyError(
                "invalid_schema",
                f"Duplicate population group key after normalization: {group_name}",
            )

        payload = _as_dict(
            raw_payload,
            field_name=f"experiment_policy.core.population_groups.{group_name}",
        )
        unknown_keys = sorted(set(payload.keys()) - POPULATION_GROUP_ALLOWED_KEYS)
        if unknown_keys:
            raise ExperimentPolicyError(
                "invalid_schema",
                "Unknown keys in experiment_policy.core.population_groups."
                f"{group_name}: {', '.join(unknown_keys)}",
            )
        missing_required = sorted(key for key in POPULATION_GROUP_REQUIRED_KEYS if key not in payload)
        if missing_required:
            raise ExperimentPolicyError(
                "missing_required",
                "Missing required keys in experiment_policy.core.population_groups."
                f"{group_name}: {', '.join(missing_required)}",
            )

        share = _coerce_share(
            payload.get("share"),
            field_name=f"experiment_policy.core.population_groups.{group_name}.share",
        )
        runtime_id = _as_required_string(
            payload.get("runtime_id"),
            field_name=f"experiment_policy.core.population_groups.{group_name}.runtime_id",
        )
        model_id = _as_required_string(
            payload.get("model_id"),
            field_name=f"experiment_policy.core.population_groups.{group_name}.model_id",
        )
        role_label = _as_optional_string(
            payload.get("role_label"),
            field_name=f"experiment_policy.core.population_groups.{group_name}.role_label",
        )
        normalized_payload: Dict[str, Any] = {
            "share": share,
            "runtime_id": runtime_id,
            "model_id": model_id,
        }
        if role_label is not None:
            normalized_payload["role_label"] = role_label

        normalized[group_name] = normalized_payload
        total_share += share

    if abs(total_share - POPULATION_SHARE_TOTAL) > POPULATION_SHARE_TOLERANCE:
        raise ExperimentPolicyError(
            "invalid_schema",
            (
                "experiment_policy.core.population_groups share sum must equal 1.0 "
                f"(received {total_share:.12f})"
            ),
        )

    return normalized


def _normalize_group_overrides(
    value: Any,
    *,
    population_groups: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    overrides = _as_dict(value, field_name="experiment_policy.core.group_overrides")
    normalized: Dict[str, Dict[str, Any]] = {}
    known_groups = set((population_groups or {}).keys())

    for raw_group_name, raw_payload in overrides.items():
        group_name = _as_required_string(
            raw_group_name,
            field_name="experiment_policy.core.group_overrides.<group_name>",
        )
        if group_name in normalized:
            raise ExperimentPolicyError(
                "invalid_schema",
                f"Duplicate group override key after normalization: {group_name}",
            )
        if known_groups and group_name not in known_groups:
            raise ExperimentPolicyError(
                "invalid_schema",
                "experiment_policy.core.group_overrides contains unknown group keys: "
                f"{group_name}",
            )

        payload = _as_dict(
            raw_payload,
            field_name=f"experiment_policy.core.group_overrides.{group_name}",
        )
        unknown_keys = sorted(set(payload.keys()) - GROUP_OVERRIDE_ALLOWED_KEYS)
        if unknown_keys:
            raise ExperimentPolicyError(
                "invalid_schema",
                "Unknown keys in experiment_policy.core.group_overrides."
                f"{group_name}: {', '.join(unknown_keys)}",
            )

        if "share" in payload:
            payload["share"] = _coerce_share(
                payload.get("share"),
                field_name=f"experiment_policy.core.group_overrides.{group_name}.share",
            )
        if "runtime_id" in payload:
            payload["runtime_id"] = _as_required_string(
                payload.get("runtime_id"),
                field_name=f"experiment_policy.core.group_overrides.{group_name}.runtime_id",
            )
        if "model_id" in payload:
            payload["model_id"] = _as_required_string(
                payload.get("model_id"),
                field_name=f"experiment_policy.core.group_overrides.{group_name}.model_id",
            )
        if "role_label" in payload:
            payload["role_label"] = _as_optional_string(
                payload.get("role_label"),
                field_name=f"experiment_policy.core.group_overrides.{group_name}.role_label",
            )
        normalized[group_name] = payload

    return normalized


def canonical_json(value: Any) -> str:
    """Canonical JSON representation used for deterministic hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_canonical_json(value: Any) -> str:
    """Return sha256 hash of canonical JSON payload."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def extract_experiment_policy(environment_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract policy envelope from environment config."""
    if not isinstance(environment_config, dict):
        return None
    raw_policy = environment_config.get("experiment_policy")
    if raw_policy is None:
        return None
    return _as_dict(raw_policy, field_name="environment_config.experiment_policy")


def validate_policy_shape(policy: Dict[str, Any]) -> Dict[str, Any]:
    """Validate policy envelope schema and return normalized payload."""
    raw = _as_dict(policy, field_name="experiment_policy")

    unknown_top_level = sorted(set(raw.keys()) - EXPERIMENT_POLICY_TOP_LEVEL_KEYS)
    if unknown_top_level:
        raise ExperimentPolicyError(
            "invalid_schema",
            "Unknown experiment_policy top-level keys: " + ", ".join(unknown_top_level),
        )

    policy_version = str(raw.get("policy_version") or DEFAULT_POLICY_VERSION).strip()
    if not policy_version:
        raise ExperimentPolicyError("missing_required", "experiment_policy.policy_version is required")

    core = raw.get("core") or {}
    core = _as_dict(core, field_name="experiment_policy.core")
    unknown_core_keys = sorted(set(core.keys()) - EXPERIMENT_POLICY_CORE_KEYS)
    if unknown_core_keys:
        raise ExperimentPolicyError(
            "invalid_schema",
            "Unknown experiment_policy.core keys: " + ", ".join(unknown_core_keys),
        )

    required_capabilities = _as_list_of_strings(
        core.get("required_capabilities"),
        field_name="experiment_policy.core.required_capabilities",
    )
    require_policy_handoff = core.get("require_policy_handoff")
    if require_policy_handoff is not None and not isinstance(require_policy_handoff, bool):
        raise ExperimentPolicyError(
            "invalid_schema",
            "experiment_policy.core.require_policy_handoff must be a boolean",
        )

    population_groups = None
    if "population_groups" in core:
        population_groups = _normalize_population_groups(core.get("population_groups"))

    group_overrides = None
    if "group_overrides" in core:
        group_overrides = _normalize_group_overrides(
            core.get("group_overrides"),
            population_groups=population_groups,
        )

    if (population_groups is not None or group_overrides is not None) and (
        POPULATION_MIX_CAPABILITY not in required_capabilities
    ):
        raise ExperimentPolicyError(
            "missing_required",
            "experiment_policy.core.population_groups/group_overrides requires "
            "experiment_policy.core.required_capabilities to include population_mix",
        )

    if "agent_overrides" in core and not isinstance(core.get("agent_overrides"), dict):
        raise ExperimentPolicyError(
            "invalid_schema",
            "experiment_policy.core.agent_overrides must be an object",
        )

    env = raw.get("env") or {}
    env = _as_dict(env, field_name="experiment_policy.env")
    normalized_env: Dict[str, Dict[str, Any]] = {}
    for env_name, env_payload in env.items():
        normalized_env[str(env_name)] = _as_dict(
            env_payload,
            field_name=f"experiment_policy.env.{env_name}",
        )

    normalized_core = dict(core)
    normalized_core["required_capabilities"] = required_capabilities
    if population_groups is not None:
        normalized_core["population_groups"] = population_groups
    if group_overrides is not None:
        normalized_core["group_overrides"] = group_overrides
    if "agent_overrides" in core:
        normalized_core["agent_overrides"] = _as_dict(
            core.get("agent_overrides"),
            field_name="experiment_policy.core.agent_overrides",
        )

    return {
        "policy_version": policy_version,
        "core": normalized_core,
        "env": normalized_env,
    }


def policy_requests_controls(policy: Optional[Dict[str, Any]], environment_name: str) -> bool:
    """Return True when policy requests env/compliance controls."""
    if not policy:
        return False
    normalized = validate_policy_shape(policy)
    core = normalized.get("core") or {}
    required_capabilities = core.get("required_capabilities") or []
    if required_capabilities:
        return True
    env_payload = (normalized.get("env") or {}).get(str(environment_name), {})
    return bool(env_payload)


def policy_requires_handoff(
    policy: Optional[Dict[str, Any]],
    environment_name: str,
    *,
    manifest_requires_handoff: bool = False,
) -> bool:
    """Resolve whether run bootstrap must enforce policy handoff."""
    if not policy:
        return bool(manifest_requires_handoff)
    normalized = validate_policy_shape(policy)
    core = normalized.get("core") or {}
    if core.get("require_policy_handoff") is not None:
        return bool(core.get("require_policy_handoff"))
    if manifest_requires_handoff:
        return True
    return policy_requests_controls(normalized, environment_name)


def _candidate_manifest_paths(
    *,
    environments_root: Path,
    environment_id: str,
) -> List[Path]:
    environment_root = environments_root / environment_id
    return [environment_root / EXPERIMENT_CAPABILITIES_MANIFEST_FILENAME]


def _default_manifest(environment_name: str, environment_id: str) -> Dict[str, Any]:
    return {
        "manifest_version": "1.0",
        "environment_name": environment_name,
        "policy_environment_key": environment_name,
        "environment_id": environment_id,
        "supported_core_keys": [],
        "supported_env_keys": [],
        "supported_capabilities": [],
        "requires_policy_handoff": False,
    }


def load_capabilities_manifest(
    *,
    environment_name: str,
    environment_id: str,
    environments_root: Path,
) -> Dict[str, Any]:
    """Load environment capabilities manifest and return normalized snapshot."""
    normalized_environment = str(environment_id or "").strip()
    if not normalized_environment:
        raise ExperimentPolicyError("missing_required", "environment id is required")

    selected_path: Optional[Path] = None
    manifest_payload: Optional[Dict[str, Any]] = None

    for candidate in _candidate_manifest_paths(
        environments_root=environments_root,
        environment_id=normalized_environment,
    ):
        if not candidate.exists() or not candidate.is_file():
            continue
        selected_path = candidate
        try:
            parsed = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ExperimentPolicyError(
                "invalid_schema",
                f"Failed to parse capabilities manifest '{candidate}': {exc}",
            ) from exc
        manifest_payload = _as_dict(
            parsed,
            field_name=f"manifest:{candidate.name}",
        )
        break

    if manifest_payload is None:
        return {
            "present": False,
            "manifest": _default_manifest(environment_name, normalized_environment),
            "manifest_hash": None,
            "manifest_path": None,
        }

    normalized_manifest = _default_manifest(environment_name, normalized_environment)
    normalized_manifest.update(manifest_payload)
    normalized_manifest["environment_name"] = str(
        normalized_manifest.get("environment_name") or environment_name
    ).strip() or environment_name
    normalized_manifest["policy_environment_key"] = str(
        normalized_manifest.get("policy_environment_key")
        or normalized_manifest.get("environment_name")
        or environment_name
    ).strip() or environment_name
    normalized_manifest["environment_id"] = str(
        normalized_manifest.get("environment_id") or normalized_environment
    ).strip() or normalized_environment
    normalized_manifest["supported_core_keys"] = _as_list_of_strings(
        normalized_manifest.get("supported_core_keys"),
        field_name="manifest.supported_core_keys",
    )
    normalized_manifest["supported_env_keys"] = _as_list_of_strings(
        normalized_manifest.get("supported_env_keys"),
        field_name="manifest.supported_env_keys",
    )
    normalized_manifest["supported_capabilities"] = _as_list_of_strings(
        normalized_manifest.get("supported_capabilities"),
        field_name="manifest.supported_capabilities",
    )
    if not isinstance(normalized_manifest.get("requires_policy_handoff"), bool):
        raise ExperimentPolicyError(
            "invalid_schema",
            "manifest.requires_policy_handoff must be a boolean",
        )

    return {
        "present": True,
        "manifest": normalized_manifest,
        "manifest_hash": hash_canonical_json(normalized_manifest),
        "manifest_path": str(selected_path) if selected_path else None,
    }


def validate_policy_against_manifest(
    policy: Dict[str, Any],
    *,
    environment_name: str,
    manifest_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate policy against environment capabilities manifest."""
    normalized_policy = validate_policy_shape(policy)
    manifest = _as_dict(manifest_snapshot.get("manifest") or {}, field_name="manifest_snapshot.manifest")
    manifest_present = bool(manifest_snapshot.get("present"))
    manifest_environment_name = str(manifest.get("environment_name") or "").strip() or environment_name
    if manifest_environment_name != environment_name:
        raise ExperimentPolicyError(
            "unsupported_feature",
            "Manifest environment_name does not match selected environment",
        )
    policy_environment_key = str(manifest.get("policy_environment_key") or environment_name).strip()
    if not policy_environment_key:
        raise ExperimentPolicyError(
            "invalid_schema",
            "Manifest policy_environment_key must be a non-empty string when provided",
        )

    env_block = normalized_policy.get("env") or {}
    unknown_env_names = sorted(name for name in env_block.keys() if name != policy_environment_key)
    if unknown_env_names:
        raise ExperimentPolicyError(
            "unsupported_feature",
            "experiment_policy.env contains unsupported environment keys: "
            + ", ".join(unknown_env_names),
        )

    env_payload = _as_dict(
        env_block.get(policy_environment_key, {}),
        field_name=f"experiment_policy.env.{policy_environment_key}",
    )
    core_block = _as_dict(normalized_policy.get("core") or {}, field_name="experiment_policy.core")

    requested_capabilities = _as_list_of_strings(
        core_block.get("required_capabilities"),
        field_name="experiment_policy.core.required_capabilities",
    )
    supported_capabilities = set(
        _as_list_of_strings(
            manifest.get("supported_capabilities"),
            field_name="manifest.supported_capabilities",
        )
    )

    if not manifest_present and (env_payload or requested_capabilities):
        raise ExperimentPolicyError(
            "unsupported_feature",
            "Environment has no capabilities manifest but policy requests experiment controls",
        )

    supported_env_keys = set(
        _as_list_of_strings(
            manifest.get("supported_env_keys"),
            field_name="manifest.supported_env_keys",
        )
    )
    unknown_env_keys = sorted(set(env_payload.keys()) - supported_env_keys)
    if unknown_env_keys:
        raise ExperimentPolicyError(
            "unsupported_feature",
            "Unsupported environment policy keys: " + ", ".join(unknown_env_keys),
        )

    supported_core_keys = set(
        _as_list_of_strings(
            manifest.get("supported_core_keys"),
            field_name="manifest.supported_core_keys",
        )
    )
    core_keys_for_manifest = sorted(
        key
        for key in core_block.keys()
        if key not in {"required_capabilities", "require_policy_handoff"}
    )
    unknown_core_keys = sorted(set(core_keys_for_manifest) - supported_core_keys)
    if unknown_core_keys:
        raise ExperimentPolicyError(
            "unsupported_feature",
            "Unsupported core policy keys for this environment: " + ", ".join(unknown_core_keys),
        )

    missing_capabilities = sorted(set(requested_capabilities) - supported_capabilities)
    if missing_capabilities:
        raise ExperimentPolicyError(
            "unsupported_feature",
            "Environment manifest does not support requested capabilities: "
            + ", ".join(missing_capabilities),
        )

    return normalized_policy


def build_policy_snapshot(
    *,
    environment_name: str,
    environment_config: Optional[Dict[str, Any]],
    environment_id: str,
    environments_root: Path,
) -> Optional[Dict[str, Any]]:
    """Build validated run policy snapshot with canonical hash metadata."""
    policy = extract_experiment_policy(environment_config)
    if policy is None:
        return None

    manifest_snapshot = load_capabilities_manifest(
        environment_name=environment_name,
        environment_id=environment_id,
        environments_root=environments_root,
    )
    normalized_policy = validate_policy_against_manifest(
        policy,
        environment_name=environment_name,
        manifest_snapshot=manifest_snapshot,
    )
    manifest = manifest_snapshot.get("manifest") or {}
    requires_handoff = policy_requires_handoff(
        normalized_policy,
        environment_name,
        manifest_requires_handoff=bool(manifest.get("requires_policy_handoff")),
    )

    return {
        "policy_json": normalized_policy,
        "policy_hash": hash_canonical_json(normalized_policy),
        "policy_version": normalized_policy.get("policy_version"),
        "required_capabilities": list(
            (normalized_policy.get("core") or {}).get("required_capabilities") or []
        ),
        "requires_handoff": requires_handoff,
        "manifest_present": bool(manifest_snapshot.get("present")),
        "manifest_hash": manifest_snapshot.get("manifest_hash"),
        "manifest_path": manifest_snapshot.get("manifest_path"),
        "manifest": manifest,
    }


def compute_assignment_hash(
    *,
    run_seed: Optional[int],
    assignment_snapshot: Dict[str, Any],
    policy_hash: Optional[str],
    manifest_hash: Optional[str],
) -> str:
    """Compute deterministic assignment hash from run seed + pinned identities."""
    assignment_map = assignment_snapshot.get("assignment_map")
    normalized_assignment_rows: List[Dict[str, Any]] = []
    if isinstance(assignment_map, dict):
        for agent_id in sorted(assignment_map.keys()):
            assignment_row = assignment_map.get(agent_id)
            assignment_payload = assignment_row if isinstance(assignment_row, dict) else {}
            normalized_assignment_rows.append(
                {
                    "agent_id": str(agent_id),
                    "population_group": assignment_payload.get("population_group"),
                    "role_label": assignment_payload.get("role_label")
                    or assignment_payload.get("role"),
                    "runtime_id": assignment_payload.get("runtime_id"),
                    "content_hash": assignment_payload.get("content_hash"),
                    "model_id": assignment_payload.get("model_id"),
                }
            )

    payload = {
        "run_seed": int(run_seed) if isinstance(run_seed, int) else run_seed,
        "environment_id": assignment_snapshot.get("environment_id"),
        "environment_content_hash": assignment_snapshot.get("environment_content_hash"),
        "runtime_id": assignment_snapshot.get("runtime_id"),
        "runtime_content_hash": assignment_snapshot.get("runtime_content_hash"),
        "agent_count": assignment_snapshot.get("agent_count"),
        "assignment_plan_hash": assignment_snapshot.get("assignment_plan_hash"),
        "assignments": normalized_assignment_rows,
        "policy_hash": policy_hash,
        "manifest_hash": manifest_hash,
        "algorithm": "sha256",
    }
    return hash_canonical_json(payload)


def environments_root_from(project_file: Path) -> Path:
    """Resolve environments root relative to this service file location."""
    file_path = project_file.resolve()
    candidates: List[Path] = []
    for depth in (2, 4):
        if len(file_path.parents) > depth:
            candidates.append(file_path.parents[depth] / "environments")
    candidates.append(Path.cwd() / "environments")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def validate_policy_shape_from_environment_config(
    environment_config: Optional[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate and canonicalize policy block inside environment config.

    Returns (normalized_policy_or_none, policy_hash_or_none).
    """
    policy = extract_experiment_policy(environment_config)
    if policy is None:
        return None, None
    normalized_policy = validate_policy_shape(policy)
    return normalized_policy, hash_canonical_json(normalized_policy)
