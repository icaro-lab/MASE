"""Helpers for environment-local run hooks."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings


DEFAULT_RUN_HOOK_TRIGGER = "on_run_start"


def _default_sim_ctrl_base() -> str:
    configured = (
        os.getenv("MASE_CONTROLLER_BASE")
        or os.getenv("CONTROLLER_BASE")
        or os.getenv("CONTROLLER_URL")
    )
    if configured:
        return str(configured).rstrip("/")
    port = str(os.getenv("PORT") or settings.port).strip() or str(settings.port)
    return f"http://controller:{port}/api/v1"


def normalize_run_hooks(environment_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    hooks = environment_manifest.get("run_hooks")
    if not isinstance(hooks, list):
        return []

    normalized: list[dict[str, Any]] = []
    for raw_hook in hooks:
        if not isinstance(raw_hook, dict):
            continue
        hook_id = str(raw_hook.get("id") or "").strip()
        script = str(raw_hook.get("script") or "").strip()
        if not hook_id or not script:
            continue
        normalized.append(
            {
                "id": hook_id,
                "trigger": str(raw_hook.get("trigger") or DEFAULT_RUN_HOOK_TRIGGER).strip()
                or DEFAULT_RUN_HOOK_TRIGGER,
                "script": script,
                "background": bool(raw_hook.get("background", True)),
                "env": raw_hook.get("env") if isinstance(raw_hook.get("env"), dict) else {},
            }
        )
    return normalized


def launch_run_hooks(
    environment_manifest: dict[str, Any],
    *,
    trigger: str,
    run_payload: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    hooks = [hook for hook in normalize_run_hooks(environment_manifest) if hook["trigger"] == trigger]
    if not hooks:
        return []

    manifest_path = Path(str(environment_manifest.get("_manifest_path") or "")).resolve(strict=False)
    if not manifest_path.is_file():
        raise ValueError("Environment manifest path is missing; cannot launch hooks")

    environment_dir = manifest_path.parent
    project_root = environment_dir.parent.parent
    run_id = str(run_payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Run payload is missing run_id; cannot launch hooks")

    launched: list[dict[str, Any]] = []
    for hook in hooks:
        hook_id = str(hook["id"])
        script_path = (environment_dir / str(hook["script"])).resolve(strict=False)
        if not script_path.is_file():
            raise ValueError(f"Run hook script not found: {hook['script']}")

        hook_log_dir = project_root / "logs" / "runs" / run_id / "hooks" / hook_id
        hook_log_dir.mkdir(parents=True, exist_ok=True)
        log_path = hook_log_dir / "hook.log"

        hook_env = os.environ.copy()
        hook_env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "MASE_HOOK_ID": hook_id,
                "MASE_RUN_ID": run_id,
                "MASE_ENVIRONMENT_ID": str(snapshot.get("environment_id") or "").strip(),
                "MASE_RUNTIME_ID": str(snapshot.get("runtime_id") or "").strip(),
                "MASE_ENVIRONMENT_URL": str(run_payload.get("environment_url") or "").strip(),
                "MASE_FRONTEND_URL": str(run_payload.get("frontend_url") or "").strip(),
                "MASE_ENVIRONMENT_DIR": str(environment_dir),
                "MASE_PROJECT_ROOT": str(project_root),
                "MASE_RUN_LOG_DIR": str(hook_log_dir),
                "MASE_RUN_PARAMS_JSON": json.dumps(snapshot.get("params") or {}, sort_keys=True),
                "MASE_POPULATION_SPECS_JSON": json.dumps(snapshot.get("population_specs") or {}, sort_keys=True),
                "MASE_LAUNCH_JSON": json.dumps(snapshot.get("launch") or {}, sort_keys=True),
                "MASE_RUN_SNAPSHOT_JSON": json.dumps(snapshot or {}, sort_keys=True),
                "MASE_CONTROLLER_BASE": _default_sim_ctrl_base(),
            }
        )
        for key, value in hook.get("env", {}).items():
            name = str(key or "").strip()
            if not name:
                continue
            hook_env[name] = str(value)

        command = ["python3", str(script_path)]
        with log_path.open("w", encoding="utf-8") as handle:
            process = subprocess.Popen(
                command,
                cwd=str(environment_dir),
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=hook_env,
            )

        launched.append(
            {
                "id": hook_id,
                "trigger": str(hook["trigger"]),
                "script": str(hook["script"]),
                "background": bool(hook.get("background", True)),
                "pid": int(process.pid),
                "log_path": str(log_path),
            }
        )

    return launched
