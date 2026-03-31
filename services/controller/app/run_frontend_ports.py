"""Helpers for reserving per-run environment frontend ports."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.database import RunBinding as RunBindingDB
from app.run_launcher import RunLauncher


def _coerce_port(value: Any) -> int | None:
    try:
        if value is None:
            return None
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port <= 0:
        return None
    return port


def extract_frontend_port(*payloads: Any) -> int | None:
    """Read a reserved frontend port from run binding payloads."""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        launch = payload.get("launch") if isinstance(payload.get("launch"), dict) else {}
        for key in ("frontend_port", "environment_frontend_port"):
            port = _coerce_port(launch.get(key))
            if port is not None:
                return port
        frontend_url = str(launch.get("frontend_url") or launch.get("environment_frontend_url") or "").strip()
        if frontend_url.startswith("http://localhost:"):
            port = _coerce_port(frontend_url.rsplit(":", 1)[-1])
            if port is not None:
                return port
    return None


def has_reserved_frontend(environment_config: dict[str, Any] | None, snapshot: dict[str, Any] | None = None) -> bool:
    """Return True when a run binding already carries a frontend reservation."""
    return extract_frontend_port(environment_config or {}, snapshot or {}) is not None


def reserve_frontend_port(
    db: Session,
    *,
    run_id: str,
    environment_has_frontend: bool,
) -> int | None:
    """Reserve a stable frontend port for a run.

    Ports are reserved across all bound runs so stopped runs can still be
    restarted later without colliding with newer runs.
    """
    if not environment_has_frontend:
        return None

    reserved_ports: set[int] = set()
    for binding in db.query(RunBindingDB).all():
        if str(binding.run_id) == str(run_id):
            continue
        port = extract_frontend_port(
            binding.environment_config if isinstance(binding.environment_config, dict) else {},
            binding.snapshot if isinstance(binding.snapshot, dict) else {},
        )
        if port is not None:
            reserved_ports.add(port)

    preferred_port = RunLauncher.get_environment_frontend_port(run_id)
    base = RunLauncher.FRONTEND_PORT_BASE
    span = RunLauncher.FRONTEND_PORT_SPAN
    start_offset = preferred_port - base

    for step in range(span):
        candidate = base + ((start_offset + step) % span)
        if candidate not in reserved_ports:
            return candidate

    raise ValueError("No available reserved frontend ports remain in the configured span")


def apply_frontend_port(
    *,
    run_id: str,
    environment_config: dict[str, Any],
    snapshot: dict[str, Any],
    frontend_port: int | None,
) -> None:
    """Persist a resolved frontend port into environment config and snapshot."""
    if frontend_port is None:
        return

    frontend_url = RunLauncher.get_environment_frontend_url(run_id, frontend_port=frontend_port)

    for payload in (environment_config, snapshot):
        launch = payload.get("launch") if isinstance(payload.get("launch"), dict) else {}
        launch["frontend_port"] = int(frontend_port)
        launch["frontend_url"] = frontend_url
        payload["launch"] = launch
