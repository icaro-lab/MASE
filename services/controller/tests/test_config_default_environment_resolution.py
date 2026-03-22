"""Unit tests for default environment resolution semantics."""
from pathlib import Path

import pytest

from app.config import Settings


def _touch_environment_manifest(base_dir: Path, environment_id: str) -> None:
    environment_dir = base_dir / environment_id
    environment_dir.mkdir(parents=True, exist_ok=True)
    (environment_dir / "environment.yaml").write_text(
        f"id: {environment_id}\nname: {environment_id}\nruntime: openclaw\n",
        encoding="utf-8",
    )


@pytest.mark.unit
def test_get_default_environment_prefers_explicit_config(tmp_path: Path) -> None:
    environments_root = tmp_path / "packages" / "environments"
    _touch_environment_manifest(environments_root, "moltbook")

    settings_obj = Settings(
        packages_path=str(tmp_path / "packages"),
        default_environment_id="environment/moltbook@1.0.0",
    )
    assert settings_obj.get_default_environment_id() == "moltbook"


@pytest.mark.unit
def test_get_default_environment_discovers_single_environment(tmp_path: Path) -> None:
    environments_root = tmp_path / "packages" / "environments"
    _touch_environment_manifest(environments_root, "demo-env")

    settings_obj = Settings(packages_path=str(tmp_path / "packages"))

    assert settings_obj.get_default_environment_id() == "demo-env"


@pytest.mark.unit
def test_get_default_environment_raises_on_ambiguous_discovery(tmp_path: Path) -> None:
    environments_root = tmp_path / "packages" / "environments"
    _touch_environment_manifest(environments_root, "moltbook")
    _touch_environment_manifest(environments_root, "tmpmod-env-20260221b")

    settings_obj = Settings(packages_path=str(tmp_path / "packages"))

    with pytest.raises(ValueError, match="Multiple environments discovered without an explicit default"):
        settings_obj.get_default_environment_id()
