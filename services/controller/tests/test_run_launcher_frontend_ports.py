"""Unit tests for manifest-driven run launcher wiring."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
import yaml

_RUN_LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "app" / "run_launcher.py"
_RUN_LAUNCHER_SPEC = importlib.util.spec_from_file_location(
    "run_launcher_test_subject",
    _RUN_LAUNCHER_PATH,
)
assert _RUN_LAUNCHER_SPEC and _RUN_LAUNCHER_SPEC.loader
sys.path.insert(0, str(_RUN_LAUNCHER_PATH.parent.parent))
_RUN_LAUNCHER_MODULE = importlib.util.module_from_spec(_RUN_LAUNCHER_SPEC)
_RUN_LAUNCHER_SPEC.loader.exec_module(_RUN_LAUNCHER_MODULE)
RunLaunchConfig = _RUN_LAUNCHER_MODULE.RunLaunchConfig
RunLauncher = _RUN_LAUNCHER_MODULE.RunLauncher


def _sample_launch_config(*, frontend_compose_file: str | None = None) -> dict[str, object]:
    frontend_service: dict[str, object] = {
        "service_name": "environment-frontend",
        "container_prefix": "environment-frontend",
        "port": 3000,
    }
    if frontend_compose_file:
        frontend_service["compose_file"] = frontend_compose_file

    return {
        "environment_service": {
            "compose_file": "environments/moltbook/compose.run.yml",
            "service_name": "environment-backend",
            "container_prefix": "environment-backend",
            "port": 8000,
        },
        "agent_worker_service": {
            "compose_file": "compose/stacks/agents.yml",
            "service_name": "agent-worker",
            "container_prefix": "environment-agents",
            "port": 8000,
        },
        "frontend_service": frontend_service,
        "images": {
            "backend": {
                "kind": "backend",
                "repository": "mase-moltbook",
                "dockerfile": "environments/moltbook/backend/Dockerfile",
                "context": "environments/moltbook",
            },
            "frontend": {
                "kind": "frontend",
                "repository": "mase-moltbook-frontend",
                "dockerfile": "environments/moltbook/frontend/Dockerfile",
                "context": "environments/moltbook/frontend",
            },
        },
    }


@pytest.mark.unit
def test_environment_frontend_port_uses_runtime_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "wt-alpha")
    alpha_port = RunLauncher.get_environment_frontend_port("run-123")

    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "wt-beta")
    beta_port = RunLauncher.get_environment_frontend_port("run-123")

    assert alpha_port != beta_port


@pytest.mark.unit
def test_generate_run_override_keeps_frontend_port_out_of_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_root = tmp_path / "compose"
    launcher = RunLauncher(compose_base_path=str(compose_root))
    launcher.overrides_path = str(compose_root / "stacks" / "run-overrides")
    Path(launcher.overrides_path).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(launcher, "resolve_environment_launch", lambda _environment_id: _sample_launch_config())

    config = RunLaunchConfig(
        run_id="run-123",
        environment_id="moltbook",
    )

    override_file = launcher.generate_run_override(config)
    override_payload = yaml.safe_load(Path(override_file).read_text(encoding="utf-8"))

    frontend_payload = override_payload["services"]["environment-frontend"]
    assert "ports" not in frontend_payload

    frontend_port = launcher.get_environment_frontend_port(config.run_id)
    backend_env = override_payload["services"]["environment-backend"]["environment"]
    assert backend_env["ENV_FRONTEND_PORT"] == str(frontend_port)
    assert backend_env["ENVIRONMENT_ID"] == "moltbook"

    agent_env = override_payload["services"]["agent-worker"]["environment"]
    assert agent_env["ENVIRONMENT_ID"] == "moltbook"


@pytest.mark.unit
def test_get_compose_files_includes_optional_frontend_compose_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_root = tmp_path / "compose"
    launcher = RunLauncher(compose_base_path=str(compose_root))
    launcher.overrides_path = str(compose_root / "stacks" / "run-overrides")
    Path(launcher.overrides_path).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        launcher,
        "resolve_environment_launch",
        lambda _environment_id: _sample_launch_config(
            frontend_compose_file="environments/moltbook/frontend.compose.yml"
        ),
    )

    config = RunLaunchConfig(
        run_id="run-123",
        environment_id="moltbook",
    )
    files = launcher._get_compose_files(config)

    assert files == [
        "environments/moltbook/compose.run.yml",
        "compose/stacks/agents.yml",
        "environments/moltbook/frontend.compose.yml",
    ]


@pytest.mark.unit
def test_launch_run_forwards_manifest_image_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_root = tmp_path / "compose"
    launcher = RunLauncher(compose_base_path=str(compose_root))
    launcher.overrides_path = str(compose_root / "stacks" / "run-overrides")
    Path(launcher.overrides_path).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(launcher, "resolve_environment_launch", lambda _environment_id: _sample_launch_config())
    monkeypatch.setattr(launcher, "generate_run_override", lambda *_args, **_kwargs: "override.yml")
    monkeypatch.setattr(
        launcher,
        "_get_compose_files",
        lambda _config: [
            "environments/moltbook/compose.run.yml",
            "compose/stacks/agents.yml",
        ],
    )
    monkeypatch.setattr(launcher, "get_service_urls", lambda *_args, **_kwargs: {})

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "started"}

    def _fake_post(url: str, json: dict[str, object], timeout: float) -> _Response:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("httpx.post", _fake_post)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "wt-alpha")
    monkeypatch.setenv("AGENT_WORKER_IMAGE", "mase-agent-launcher:wt-alpha")
    monkeypatch.setattr(_RUN_LAUNCHER_MODULE.settings, "openrouter_api_key", "sk-openrouter")

    config = RunLaunchConfig(
        run_id="run-123",
        environment_id="moltbook",
    )

    result = launcher.launch_run(config)

    assert result["status"] == "launched"
    env_vars = captured["json"]["env_vars"]
    assert env_vars["AGENT_WORKER_IMAGE"] == "mase-agent-launcher:wt-alpha"
    assert env_vars["ENVIRONMENT_BACKEND_IMAGE"] == "mase-moltbook:wt-alpha"
    assert env_vars["ENVIRONMENT_FRONTEND_IMAGE"] == "mase-moltbook-frontend:wt-alpha"
    assert env_vars["AGENT_LAUNCHER_OPENROUTER_API_KEY"] == "sk-openrouter"
    assert env_vars["OPENROUTER_API_KEY"] == "sk-openrouter"
