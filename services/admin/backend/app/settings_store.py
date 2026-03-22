"""Persistent admin dashboard settings helpers."""

import json
from pathlib import Path
import threading
from typing import Optional, Tuple

from .config import Settings, get_settings


_SETTINGS_FILE_LOCK = threading.Lock()


def _read_custom_key(path: Path) -> Optional[str]:
    """Read a plain-text or JSON key payload from storage."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None

    raw_key: Optional[str] = None
    if isinstance(payload, str):
        raw_key = payload
    elif isinstance(payload, dict):
        candidate = payload.get("openrouter_api_key")
        if isinstance(candidate, str):
            raw_key = candidate

    if not raw_key:
        return None

    key = raw_key.strip()
    return key or None


def _write_custom_key(path: Path, value: str) -> None:
    """Persist the configured baseline key to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"openrouter_api_key": value}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    path.chmod(0o600)


def mask_openrouter_key(value: str) -> str:
    if len(value) <= 8:
        return '*' * len(value)
    return f"{value[:4]}...{value[-4:]}"


def get_openrouter_fallback_key(settings: Optional[Settings] = None) -> Optional[str]:
    """Return fallback OpenRouter key, preferring explicit UI-configured storage."""
    active_settings = settings or get_settings()
    settings_path = Path(active_settings.openrouter_api_key_file)

    with _SETTINGS_FILE_LOCK:
        custom_key = _read_custom_key(settings_path)

    if custom_key:
        return custom_key

    env_key = active_settings.openrouter_api_key_fallback.strip()
    return env_key or None


def update_openrouter_fallback_key(
    value: Optional[str],
    settings: Optional[Settings] = None,
) -> Tuple[Optional[str], str]:
    """Update fallback key storage and return (resolved_key, source)."""
    active_settings = settings or get_settings()
    settings_path = Path(active_settings.openrouter_api_key_file)
    key_value = (value or "").strip()

    with _SETTINGS_FILE_LOCK:
        if key_value:
            _write_custom_key(settings_path, key_value)
            return key_value, "custom"

        if settings_path.exists():
            try:
                settings_path.unlink()
            except OSError:
                pass

        env_key = active_settings.openrouter_api_key_fallback.strip()
        if env_key:
            return env_key, "environment"

        return None, "missing"


def describe_openrouter_fallback(settings: Optional[Settings] = None) -> dict[str, object]:
    """Return non-sensitive OpenRouter key status for API responses."""
    active_settings = settings or get_settings()
    settings_path = Path(active_settings.openrouter_api_key_file)
    env_key = active_settings.openrouter_api_key_fallback.strip()

    with _SETTINGS_FILE_LOCK:
        custom_key = _read_custom_key(settings_path)

    if custom_key:
        return {
            "openrouter_api_key_configured": True,
            "openrouter_api_key_masked": mask_openrouter_key(custom_key),
            "source": "custom",
        }

    if env_key:
        return {
            "openrouter_api_key_configured": True,
            "openrouter_api_key_masked": mask_openrouter_key(env_key),
            "source": "environment",
        }

    return {
        "openrouter_api_key_configured": False,
        "openrouter_api_key_masked": None,
        "source": "missing",
    }
