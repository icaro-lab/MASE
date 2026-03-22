"""Unit tests for environment-local run hook launching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.environment_hooks import launch_run_hooks, normalize_run_hooks


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.unit
def test_normalize_run_hooks_defaults_trigger_and_background() -> None:
    payload = normalize_run_hooks(
        {
            "run_hooks": [
                {
                    "id": "seed_default_world",
                    "script": "hooks/run_seed_default_world.py",
                }
            ]
        }
    )

    assert payload == [
        {
            "id": "seed_default_world",
            "trigger": "on_run_start",
            "script": "hooks/run_seed_default_world.py",
            "background": True,
            "env": {},
        }
    ]


@pytest.mark.unit
def test_launch_run_hooks_injects_run_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment_dir = tmp_path / "environments" / "moltbook"
    script_path = environment_dir / "hooks" / "run_seed_default_world.py"
    _write(environment_dir / "environment.yaml", "id: moltbook\nruntime: openclaw\n")
    _write(script_path, "print('ok')\n")

    captured: dict[str, object] = {}

    class _FakeProcess:
        pid = 4242

    def _fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return _FakeProcess()

    monkeypatch.setattr("app.environment_hooks.subprocess.Popen", _fake_popen)

    manifest = {
        "_manifest_path": str(environment_dir / "environment.yaml"),
        "run_hooks": [
            {
                "id": "seed_default_world",
                "trigger": "on_run_start",
                "script": "hooks/run_seed_default_world.py",
            }
        ],
    }
    snapshot = {
        "environment_id": "moltbook",
        "runtime_id": "openclaw",
        "params": {},
        "population_specs": {"resident": {"count": 1}},
        "launch": {"environment_id": "moltbook"},
    }
    run_payload = {
        "run_id": "run-123",
        "environment_url": "http://moltbook-backend-run-123:8000",
        "frontend_url": "http://localhost:3016/runs/run-123/stats",
    }

    launched = launch_run_hooks(
        manifest,
        trigger="on_run_start",
        run_payload=run_payload,
        snapshot=snapshot,
    )

    assert launched[0]["id"] == "seed_default_world"
    assert launched[0]["pid"] == 4242
    assert captured["command"] == ["python3", str(script_path.resolve(strict=False))]
    assert captured["cwd"] == str(environment_dir)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["MASE_RUN_ID"] == "run-123"
    assert env["MASE_ENVIRONMENT_ID"] == "moltbook"
    assert env["MASE_RUNTIME_ID"] == "openclaw"
    assert env["MASE_ENVIRONMENT_URL"] == "http://moltbook-backend-run-123:8000"
    assert json.loads(env["MASE_RUN_PARAMS_JSON"]) == {}
    assert json.loads(env["MASE_POPULATION_SPECS_JSON"]) == {"resident": {"count": 1}}
