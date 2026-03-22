"""Runtime/environment package hashing helpers."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings


logger = logging.getLogger(__name__)
PACKAGE_KIND_RUNTIME = "runtime"
PACKAGE_KIND_ENVIRONMENT = "environment"
IGNORED_PACKAGE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    ".svelte-kit",
    "build",
    "dist",
    ".venv",
    "venv",
}


def normalize_environment_ref(environment_ref: Optional[str]) -> str:
    cleaned = str(environment_ref or "").strip().strip("/")
    if cleaned.startswith("environment/"):
        cleaned = cleaned[len("environment/") :]
    if cleaned.startswith("environments/"):
        cleaned = cleaned[len("environments/") :]
    normalized = Path(cleaned).name or cleaned
    if "@" in normalized:
        normalized = normalized.split("@", 1)[0]
    return normalized


def project_root() -> Path:
    file_path = Path(__file__).resolve()
    candidates: list[Path] = []
    for depth in (1, 3):
        if len(file_path.parents) > depth:
            candidates.append(file_path.parents[depth])
    packages_root_setting = str(settings.packages_path or "").strip()
    if packages_root_setting:
        candidates.append(Path(packages_root_setting).parent)
    candidates.append(Path.cwd())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def package_root(kind: str, package_id: str) -> Path:
    root = project_root()
    if kind == PACKAGE_KIND_RUNTIME:
        return root / "runtimes" / package_id
    if kind == PACKAGE_KIND_ENVIRONMENT:
        return root / "environments" / package_id
    raise ValueError(f"Unsupported package kind: {kind}")


def _fallback_package_root(kind: str, package_id: str) -> Path:
    cwd = Path.cwd()
    if kind == PACKAGE_KIND_RUNTIME:
        return cwd / "runtimes" / package_id
    if kind == PACKAGE_KIND_ENVIRONMENT:
        return cwd / "environments" / package_id
    raise ValueError(f"Unsupported package kind: {kind}")


def _iter_package_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists() or not root.is_dir():
        return files
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in IGNORED_PACKAGE_DIRS or part == "versions" for part in rel.parts):
            continue
        if path.name in {".package-meta.json", ".runtime-meta.json", ".environment-meta.json"}:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _sha256_package_tree(root: Path) -> str:
    hasher = hashlib.sha256()
    for file_path in _iter_package_files(root):
        rel = file_path.relative_to(root).as_posix().encode("utf-8")
        hasher.update(rel)
        hasher.update(b"\0")
        hasher.update(file_path.read_bytes())
        hasher.update(b"\0")
    return f"sha256:{hasher.hexdigest()}"


def resolve_package_hash_from_filesystem(*, kind: str, package_id: str) -> Optional[str]:
    root = package_root(kind, package_id)
    if (not root.exists() or not root.is_dir()) and Path.cwd() != project_root():
        root = _fallback_package_root(kind, package_id)
    if not root.exists() or not root.is_dir():
        return None
    package_hash = _sha256_package_tree(root)
    if not package_hash:
        return None
    return package_hash


def resolve_package_hash(
    _db: Session | None,
    *,
    kind: str,
    package_id: str,
) -> str:
    package_hash = resolve_package_hash_from_filesystem(kind=kind, package_id=package_id)
    if not package_hash:
        raise ValueError(f"Package not found: kind={kind}, package_id={package_id}")
    return package_hash


def resolve_runtime_heartbeat_interval(*, runtime_id: str) -> Optional[str]:
    normalized_runtime_id = str(runtime_id or "").strip()
    if not normalized_runtime_id:
        return None

    config_path = package_root(PACKAGE_KIND_RUNTIME, normalized_runtime_id) / "config.json"
    if not config_path.exists() or not config_path.is_file():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    heartbeat_block = payload.get("heartbeat")
    if not isinstance(heartbeat_block, dict):
        return None

    every_value = str(heartbeat_block.get("every") or "").strip()
    if every_value:
        return every_value

    legacy_seconds = heartbeat_block.get("interval_seconds")
    if legacy_seconds is None:
        return None
    try:
        seconds = int(float(legacy_seconds))
    except (TypeError, ValueError):
        return None
    return f"{seconds}s" if seconds > 0 else None
