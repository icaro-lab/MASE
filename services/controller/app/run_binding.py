"""Run-owned runtime/environment binding helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.database import Run as RunDB
from app.database import RunBinding as RunBindingDB


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _hash_snapshot(snapshot: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def normalize_environment_ref(environment_ref: Any) -> str | None:
    raw = str(environment_ref or "").strip().strip("/")
    if not raw:
        return None
    if raw.startswith("environment/"):
        raw = raw[len("environment/") :]
    if raw.startswith("environments/"):
        raw = raw[len("environments/") :]
    raw = raw.split("/")[-1]
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    normalized = raw.strip()
    return normalized or None


def resolve_binding(run: RunDB, db: Session) -> RunBindingDB | None:
    binding = getattr(run, "binding", None)
    if binding is not None:
        return binding
    if not isinstance(run, RunDB):
        return None
    try:
        query = db.query(RunBindingDB).filter(RunBindingDB.run_id == run.run_id)
        if hasattr(query, "first"):
            return query.first()
    except Exception:
        return None
    return None


def build_run_context(run: RunDB, db: Session) -> dict[str, Any]:
    binding = resolve_binding(run, db)
    environment_config = _as_dict(getattr(binding, "environment_config", None))
    snapshot = _as_dict(getattr(binding, "snapshot", None))
    if not snapshot:
        launch = _as_dict(environment_config.get("launch"))
        snapshot = {
            "environment_id": _normalize_text(getattr(binding, "environment_id", None)),
            "runtime_id": _normalize_text(getattr(binding, "runtime_id", None)),
            "launch": launch,
            "params": _as_dict(environment_config.get("environment_params")),
            "population_specs": _as_dict(environment_config.get("population_specs")),
            "run_hooks": environment_config.get("run_hooks")
            if isinstance(environment_config.get("run_hooks"), list)
            else [],
            "experiment_policy": _as_dict(environment_config.get("experiment_policy")),
            "runtime_controls": {
                "heartbeat": environment_config.get("heartbeat"),
                "max_parallel_agents": environment_config.get("max_parallel_agents"),
                "max_ticks": environment_config.get("max_ticks"),
                "max_heartbeats_per_agent": environment_config.get("max_heartbeats_per_agent"),
                "runtime_limit_minutes": environment_config.get("runtime_limit_minutes"),
            },
            "seed": getattr(run, "seed", None),
        }
    snapshot_hash = _normalize_text(getattr(binding, "snapshot_hash", None)) or (
        _hash_snapshot(snapshot) if snapshot else None
    )
    return {
        "binding": binding,
        "environment_id": _normalize_text(getattr(binding, "environment_id", None)),
        "runtime_id": _normalize_text(getattr(binding, "runtime_id", None)),
        "environment_ref": _normalize_text(getattr(binding, "environment_ref", None)),
        "environment_config": environment_config,
        "snapshot": snapshot,
        "snapshot_hash": snapshot_hash,
    }


def upsert_run_binding(
    db: Session,
    *,
    run: RunDB,
    environment_id: str,
    runtime_id: str | None,
    environment_ref: str | None,
    environment_config: dict[str, Any],
    snapshot: dict[str, Any],
    snapshot_hash: str | None = None,
) -> RunBindingDB:
    binding = resolve_binding(run, db)
    if binding is None:
        binding = RunBindingDB(run_id=run.run_id, environment_id=environment_id)
        db.add(binding)

    binding.environment_id = environment_id
    binding.runtime_id = runtime_id
    binding.environment_ref = environment_ref
    binding.environment_config = environment_config
    binding.snapshot = snapshot
    binding.snapshot_hash = snapshot_hash or _hash_snapshot(snapshot)
    db.flush()
    return binding
