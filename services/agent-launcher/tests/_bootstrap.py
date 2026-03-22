"""Shared path bootstrap for agent-launcher tests."""

from __future__ import annotations

import sys
from pathlib import Path


def _find_service_root(current: Path) -> Path:
    for candidate in (current.parent, *current.parents):
        if (candidate / "app").is_dir() and (candidate / "requirements.txt").is_file():
            return candidate
    raise RuntimeError(f"Unable to resolve agent-launcher service root from {current}")


def _find_project_root(service_root: Path) -> Path:
    for candidate in service_root.parents:
        if (
            (candidate / "runtimes").is_dir()
            and (candidate / "environments").is_dir()
            and (candidate / "services" / "agent-launcher").is_dir()
        ):
            return candidate

    if (service_root / "runtimes").is_dir() and (service_root / "environments").is_dir():
        return service_root

    raise RuntimeError(f"Unable to resolve project root from {service_root}")


SERVICE_ROOT = _find_service_root(Path(__file__).resolve())
PROJECT_ROOT = _find_project_root(SERVICE_ROOT)

service_root_str = str(SERVICE_ROOT)
if service_root_str not in sys.path:
    sys.path.insert(0, service_root_str)
