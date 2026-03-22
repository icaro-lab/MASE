"""Tests for runtime/environment asset discovery and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.catalog import (
    _dedupe_paths,
    discover_environments,
    discover_runtimes,
    load_population_materialization,
    validate_environment,
)
from app.package_hashes import PACKAGE_KIND_ENVIRONMENT, resolve_package_hash_from_filesystem

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_minimal_launch_assets(tmp_path: Path, environment_id: str) -> None:
    _write(
        tmp_path / "compose" / "stacks" / "agents.yml",
        "services:\n  agent-worker:\n    image: mase-agent-launcher:latest\n",
    )
    _write(
        tmp_path / "environments" / environment_id / "compose.run.yml",
        "services:\n  environment-backend:\n    image: mase-environment:latest\n",
    )
    _write(
        tmp_path / "environments" / environment_id / "backend" / "Dockerfile",
        "FROM python:3.11-slim\n",
    )


@pytest.mark.unit
def test_discover_runtimes_reads_runtime_manifests(tmp_path: Path) -> None:
    runtimes_root = tmp_path / "runtimes"
    _write(
        runtimes_root / "openclaw" / "runtime.yaml",
        "id: openclaw\nname: OpenClaw Runtime\nagent_core: openclaw_py_core\n",
    )

    manifests = discover_runtimes([runtimes_root])

    assert len(manifests) == 1
    assert manifests[0]["id"] == "openclaw"
    assert manifests[0]["agent_core"] == "openclaw_py_core"


@pytest.mark.unit
def test_discover_environments_reads_environment_manifests(tmp_path: Path) -> None:
    environments_root = tmp_path / "environments"
    _write(
        environments_root / "moltbook" / "environment.yaml",
        "id: moltbook\nname: Moltbook\nruntime: openclaw\n"
        "params_schema: {}\nbackend_contract: {}\nenvironment_skills: []\npopulations: {}\n",
    )

    manifests = discover_environments([environments_root])

    assert len(manifests) == 1
    assert manifests[0]["id"] == "moltbook"
    assert manifests[0]["runtime"] == "openclaw"


@pytest.mark.unit
def test_discover_environments_skips_hidden_and_template_dirs(tmp_path: Path) -> None:
    environments_root = tmp_path / "environments"
    _write(
        environments_root / "_template" / "environment.yaml",
        "id: template-env\nname: Template\nruntime: openclaw\n"
        "params_schema: {}\nbackend_contract: {}\nenvironment_skills: []\npopulations: {}\n",
    )
    _write(
        environments_root / ".hidden" / "environment.yaml",
        "id: hidden-env\nname: Hidden\nruntime: openclaw\n"
        "params_schema: {}\nbackend_contract: {}\nenvironment_skills: []\npopulations: {}\n",
    )
    _write(
        environments_root / "moltbook" / "environment.yaml",
        "id: moltbook\nname: Moltbook\nruntime: openclaw\n"
        "params_schema: {}\nbackend_contract: {}\nenvironment_skills: []\npopulations: {}\n",
    )

    manifests = discover_environments([environments_root])

    assert [manifest["id"] for manifest in manifests] == ["moltbook"]


@pytest.mark.unit
def test_dedupe_paths_collapses_relative_and_absolute_duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environments_root = tmp_path / "environments"
    environments_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    deduped = _dedupe_paths(["./environments", str(environments_root)])

    assert deduped == [Path("./environments")]


@pytest.mark.unit
def test_validate_environment_requires_runtime_skills_and_population_contract(tmp_path: Path) -> None:
    runtimes_root = tmp_path / "runtimes"
    environments_root = tmp_path / "environments"
    _write_minimal_launch_assets(tmp_path, "moltbook")

    _write(
        runtimes_root / "openclaw" / "runtime.yaml",
        "id: openclaw\nname: OpenClaw Runtime\nagent_core: openclaw_py_minimal_core\n",
    )
    _write(
        environments_root / "moltbook" / "environment.yaml",
        "\n".join(
            [
                "id: moltbook",
                "name: Moltbook",
                "runtime: openclaw",
                "params_schema: {}",
                "backend_contract:",
                "  required_endpoints:",
                "    - GET /health",
                "    - GET /contract",
                "    - GET /skill.md",
                "    - POST /auth/register",
                "launch:",
                "  environment_service:",
                "    compose_file: environments/moltbook/compose.run.yml",
                "    service_name: environment-backend",
                "    container_prefix: environment-backend",
                "    port: 8000",
                "  agent_worker_service:",
                "    compose_file: compose/stacks/agents.yml",
                "    service_name: agent-worker",
                "    container_prefix: environment-agents",
                "    port: 8000",
                "  images:",
                "    backend:",
                "      repository: mase-moltbook",
                "      dockerfile: environments/moltbook/backend/Dockerfile",
                "      context: environments/moltbook",
                "environment_skills:",
                "  - get_feed",
                "populations:",
                "  voter:",
                "    path: populations/voter",
                "    tools_manifest: populations/voter/TOOLS.yaml",
                "run_hooks:",
                "  - id: seed_default_world",
                "    script: hooks/run_seed_default_world.py",
                "",
            ]
        ),
    )
    _write(environments_root / "moltbook" / "skill.md", "# Moltbook\n")
    _write(
        environments_root / "moltbook" / "hooks" / "run_seed_default_world.py",
        "print('ok')\n",
    )
    _write(environments_root / "moltbook" / "skills" / "get_feed" / "skill.yaml", "id: get_feed\n")
    _write(environments_root / "moltbook" / "skills" / "get_feed" / "SKILL.md", "# get_feed\n")
    _write(environments_root / "moltbook" / "populations" / "voter" / "AGENTS.md", "# AGENTS.md\n")
    _write(environments_root / "moltbook" / "populations" / "voter" / "HEARTBEAT.md", "# HEARTBEAT.md\n")
    _write(
        environments_root / "moltbook" / "populations" / "voter" / "TOOLS.yaml",
        "runtime_tools: []\nenvironment_skills:\n  - get_feed\n",
    )

    result = validate_environment(
        "moltbook",
        environment_roots=[environments_root],
        runtime_roots=[runtimes_root],
    )

    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.unit
def test_validate_environment_requires_openclaw_skill_doc_and_core_endpoints(tmp_path: Path) -> None:
    runtimes_root = tmp_path / "runtimes"
    environments_root = tmp_path / "environments"
    _write_minimal_launch_assets(tmp_path, "demo-env")

    _write(
        runtimes_root / "openclaw" / "runtime.yaml",
        "id: openclaw\nname: OpenClaw Runtime\nagent_core: openclaw_py_minimal_core\n",
    )
    _write(
        environments_root / "demo-env" / "environment.yaml",
        "\n".join(
            [
                "id: demo-env",
                "name: Demo Env",
                "runtime: openclaw",
                "params_schema: {}",
                "backend_contract:",
                "  required_endpoints:",
                "    - GET /health",
                "launch:",
                "  environment_service:",
                "    compose_file: environments/demo-env/compose.run.yml",
                "    service_name: environment-backend",
                "    container_prefix: environment-backend",
                "    port: 8000",
                "  agent_worker_service:",
                "    compose_file: compose/stacks/agents.yml",
                "    service_name: agent-worker",
                "    container_prefix: environment-agents",
                "    port: 8000",
                "  images:",
                "    backend:",
                "      repository: mase-demo-env",
                "      dockerfile: environments/demo-env/backend/Dockerfile",
                "      context: environments/demo-env",
                "environment_skills:",
                "  - get_feed",
                "populations:",
                "  resident:",
                "    path: populations/resident",
                "    tools_manifest: populations/resident/TOOLS.yaml",
                "",
            ]
        ),
    )
    _write(environments_root / "demo-env" / "skills" / "get_feed" / "skill.yaml", "id: get_feed\n")
    _write(environments_root / "demo-env" / "skills" / "get_feed" / "SKILL.md", "# get_feed\n")
    _write(environments_root / "demo-env" / "populations" / "resident" / "AGENTS.md", "# AGENTS.md\n")
    _write(environments_root / "demo-env" / "populations" / "resident" / "HEARTBEAT.md", "# HEARTBEAT.md\n")
    _write(
        environments_root / "demo-env" / "populations" / "resident" / "TOOLS.yaml",
        "runtime_tools: []\nenvironment_skills:\n  - get_feed\n",
    )

    result = validate_environment(
        "demo-env",
        environment_roots=[environments_root],
        runtime_roots=[runtimes_root],
    )

    assert result["valid"] is False
    assert "missing required runtime bootstrap document: skill.md" in result["errors"]
    assert "backend_contract.required_endpoints must include GET /contract" in result["errors"]
    assert "backend_contract.required_endpoints must include GET /skill.md" in result["errors"]
    assert "backend_contract.required_endpoints must include POST /auth/register" in result["errors"]


@pytest.mark.unit
def test_resolve_package_hash_supports_environment_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environments_root = tmp_path / "environments"
    _write(
        environments_root / "demo-env" / "environment.yaml",
        "id: demo-env\nname: Demo Env\nruntime: openclaw\nparams_schema: {}\n",
    )
    _write(environments_root / "demo-env" / "compose.run.yml", "services: {}\n")
    _write(environments_root / "demo-env" / "skill.md", "# Demo Env\n")
    _write(
        environments_root / "demo-env" / "backend" / "app" / "main.py",
        "print('demo-env')\n",
    )
    monkeypatch.chdir(tmp_path)

    resolved = resolve_package_hash_from_filesystem(
        kind=PACKAGE_KIND_ENVIRONMENT,
        package_id="demo-env",
    )

    assert resolved is not None
    assert resolved.startswith("sha256:")


@pytest.mark.unit
def test_load_population_materialization_reads_workspace_overrides(tmp_path: Path) -> None:
    environments_root = tmp_path / "environments"
    _write(
        environments_root / "moltbook" / "environment.yaml",
        "\n".join(
            [
                "id: moltbook",
                "name: Moltbook",
                "runtime: openclaw",
                "params_schema: {}",
                "backend_contract: {}",
                "environment_skills:",
                "  - get_feed",
                "populations:",
                "  voter:",
                "    path: populations/voter",
                "    tools_manifest: populations/voter/TOOLS.yaml",
                "",
            ]
        ),
    )
    _write(
        environments_root / "moltbook" / "skills" / "get_feed" / "skill.yaml",
        "\n".join(
            [
                "id: get_feed",
                "description: Read visible feed cards.",
                "http:",
                "  method: GET",
                "  path: /api/v1/feed",
                "",
            ]
        ),
    )
    _write(environments_root / "moltbook" / "skills" / "get_feed" / "SKILL.md", "# get_feed\n")
    _write(environments_root / "moltbook" / "populations" / "voter" / "AGENTS.md", "# AGENTS.md\n")
    _write(environments_root / "moltbook" / "populations" / "voter" / "HEARTBEAT.md", "# HEARTBEAT.md\n")
    _write(
        environments_root / "moltbook" / "populations" / "voter" / "TOOLS.yaml",
        "runtime_tools: []\nenvironment_skills:\n  - get_feed\n",
    )

    payload = load_population_materialization(
        "moltbook",
        "voter",
        environment_roots=[environments_root],
    )

    assert payload["agents"] == "# AGENTS.md\n"
    assert payload["heartbeat"] == "# HEARTBEAT.md\n"
    assert "## Environment Skills" in payload["tools"]
    assert "get_feed" in payload["tools"]


@pytest.mark.unit
def test_validate_environment_fails_when_runtime_missing(tmp_path: Path) -> None:
    environments_root = tmp_path / "environments"
    _write_minimal_launch_assets(tmp_path, "moltbook")
    _write(
        environments_root / "moltbook" / "environment.yaml",
        "\n".join(
            [
                "id: moltbook",
                "name: Moltbook",
                "runtime: missing-runtime",
                "params_schema: {}",
                "backend_contract: {}",
                "launch:",
                "  environment_service:",
                "    compose_file: environments/moltbook/compose.run.yml",
                "    service_name: environment-backend",
                "    container_prefix: environment-backend",
                "    port: 8000",
                "  agent_worker_service:",
                "    compose_file: compose/stacks/agents.yml",
                "    service_name: agent-worker",
                "    container_prefix: environment-agents",
                "    port: 8000",
                "  images:",
                "    backend:",
                "      repository: mase-moltbook",
                "      dockerfile: environments/moltbook/backend/Dockerfile",
                "      context: environments/moltbook",
                "environment_skills: []",
                "populations:",
                "  voter:",
                "    path: populations/voter",
                "",
            ]
        ),
    )

    result = validate_environment(
        "moltbook",
        environment_roots=[environments_root],
        runtime_roots=[tmp_path / "runtimes"],
    )

    assert result["valid"] is False
    assert any("unknown runtime" in error for error in result["errors"])


@pytest.mark.unit
def test_validate_environment_requires_hook_script_but_not_population_runtime_override(tmp_path: Path) -> None:
    runtimes_root = tmp_path / "runtimes"
    environments_root = tmp_path / "environments"
    _write_minimal_launch_assets(tmp_path, "moltbook")
    _write(
        runtimes_root / "openclaw" / "runtime.yaml",
        "id: openclaw\nname: OpenClaw Runtime\nagent_core: openclaw_py_minimal_core\n",
    )
    _write(
        environments_root / "moltbook" / "environment.yaml",
        "\n".join(
            [
                "id: moltbook",
                "name: Moltbook",
                "runtime: openclaw",
                "params_schema: {}",
                "backend_contract: {}",
                "launch:",
                "  environment_service:",
                "    compose_file: environments/moltbook/compose.run.yml",
                "    service_name: environment-backend",
                "    container_prefix: environment-backend",
                "    port: 8000",
                "  agent_worker_service:",
                "    compose_file: compose/stacks/agents.yml",
                "    service_name: agent-worker",
                "    container_prefix: environment-agents",
                "    port: 8000",
                "  images:",
                "    backend:",
                "      repository: mase-moltbook",
                "      dockerfile: environments/moltbook/backend/Dockerfile",
                "      context: environments/moltbook",
                "environment_skills:",
                "  - get_feed",
                "populations:",
                "  voter:",
                "    path: populations/voter",
                "    tools_manifest: populations/voter/TOOLS.yaml",
                "run_hooks:",
                "  - id: seed_default_world",
                "    script: hooks/run_seed_default_world.py",
                "",
            ]
        ),
    )
    (environments_root / "moltbook" / "skills" / "get_feed").mkdir(parents=True, exist_ok=True)
    _write(environments_root / "moltbook" / "skills" / "get_feed" / "skill.yaml", "id: get_feed\n")
    _write(environments_root / "moltbook" / "skills" / "get_feed" / "SKILL.md", "# get_feed\n")
    _write(environments_root / "moltbook" / "populations" / "voter" / "AGENTS.md", "# AGENTS.md\n")
    _write(environments_root / "moltbook" / "populations" / "voter" / "HEARTBEAT.md", "# HEARTBEAT.md\n")
    _write(
        environments_root / "moltbook" / "populations" / "voter" / "TOOLS.yaml",
        "runtime_tools: []\nenvironment_skills:\n  - get_feed\n",
    )

    result = validate_environment(
        "moltbook",
        environment_roots=[environments_root],
        runtime_roots=[runtimes_root],
    )

    assert result["valid"] is False
    assert "run hook script does not exist: hooks/run_seed_default_world.py" in result["errors"]


@pytest.mark.unit
def test_validate_public_moltbook_environment_assets() -> None:
    result = validate_environment(
        "moltbook",
        environment_roots=[PROJECT_ROOT / "environments"],
        runtime_roots=[PROJECT_ROOT / "runtimes"],
    )

    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.unit
def test_repo_moltbook_environment_is_discoverable_and_valid() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    environments_root = repo_root / "environments"
    runtimes_root = repo_root / "runtimes"

    manifests = discover_environments([environments_root])

    assert any(item["id"] == "moltbook" for item in manifests)

    result = validate_environment(
        "moltbook",
        environment_roots=[environments_root],
        runtime_roots=[runtimes_root],
    )

    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.unit
def test_repo_moltbook_population_materialization_uses_environment_local_files() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    environments_root = repo_root / "environments"

    payload = load_population_materialization(
        "moltbook",
        "resident",
        environment_roots=[environments_root],
    )

    assert "resident account inside Moltbook" in payload["agents"]
    assert "create_post" in payload["tools"]
    assert "get_feed" in payload["tools"]


@pytest.mark.unit
def test_validate_public_hello_world_environment_assets() -> None:
    result = validate_environment(
        "hello-world",
        environment_roots=[PROJECT_ROOT / "environments"],
        runtime_roots=[PROJECT_ROOT / "runtimes"],
    )

    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.unit
def test_repo_hello_world_environment_is_discoverable_and_valid() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    environments_root = repo_root / "environments"
    runtimes_root = repo_root / "runtimes"

    manifests = discover_environments([environments_root])

    assert any(item["id"] == "hello-world" for item in manifests)

    result = validate_environment(
        "hello-world",
        environment_roots=[environments_root],
        runtime_roots=[runtimes_root],
    )

    assert result["valid"] is True
    assert result["errors"] == []
