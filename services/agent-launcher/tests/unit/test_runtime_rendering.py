"""Unit tests for runtime package rendering and required-value validation."""

import json
from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app import agent_fs as agent_fs_module
from app.agent_fs import AgentFilesystem, STANDARD_FILES
from app.config import settings
from app.routes import launcher


def _seed_runtime_package(runtimes_root: Path) -> None:
    runtime_root = runtimes_root / "openclaw"
    workspace_root = runtime_root / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)

    for filename in STANDARD_FILES:
        if filename == "IDENTITY.md":
            content = "# IDENTITY\nAgent {{AGENT_ID}}\nUnknown={{UNKNOWN_FIELD}}\n"
        elif filename == "USER.md":
            content = "# USER\nRun {{RUN_ID}}\n"
        else:
            content = f"# {filename}\n"
        (workspace_root / filename).write_text(content, encoding="utf-8")

    config_payload = {
        "agent_id": "{{AGENT_ID}}",
        "agent_name": "{{AGENT_NAME}}",
        "environment_url": "{{ENVIRONMENT_URL}}",
        "optional_marker": "{{OPTIONAL_CONFIG_TOKEN}}",
    }
    (runtime_root / "config.json").write_text(
        json.dumps(config_payload, indent=2),
        encoding="utf-8",
    )


def _seed_public_environment(env_root_base: Path, name: str = "moltbook") -> None:
    env_root = env_root_base / name
    env_root.mkdir(parents=True, exist_ok=True)
    (env_root / "skill.md").write_text("# Public Skill\nSee HEARTBEAT.md and RULES.md.\n", encoding="utf-8")
    (env_root / "heartbeat.md").write_text("# Public Heartbeat\n", encoding="utf-8")
    (env_root / "messaging.md").write_text("# Public Messaging\n", encoding="utf-8")
    (env_root / "rules.md").write_text("# Public Rules\n", encoding="utf-8")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_agent_directory_renders_placeholders_and_records_optional_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    runtimes_root = tmp_path / "runtimes"
    environments_root = tmp_path / "environments"
    _seed_runtime_package(runtimes_root)
    _seed_public_environment(environments_root)

    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(agent_fs_module, "RUNTIME_PACKAGE_PATH", runtimes_root)
    monkeypatch.setattr(agent_fs_module, "ENVIRONMENT_PACKAGE_PATH", environments_root)

    fs = AgentFilesystem("agent-1", run_id="run-1")
    result = await fs.create_agent_directory(
        runtime_id="openclaw",
        agent_name="agent-name-1",
        environment_url="http://environment:8000",
        environment_name="moltbook",
    )

    assert result["created"] is True
    warnings = result.get("render_warnings") or []
    assert any(item.get("file") == "IDENTITY.md" for item in warnings)
    assert any(item.get("file") == "config.json" for item in warnings)

    identity_text = (fs.workspace_path / "IDENTITY.md").read_text(encoding="utf-8")
    assert "{{UNKNOWN_FIELD}}" not in identity_text
    assert "Agent agent-1" in identity_text

    config_text = (fs.agent_path / "config.json").read_text(encoding="utf-8")
    assert "{{ENVIRONMENT_URL}}" not in config_text
    assert "http://environment:8000" in config_text

    workspace_heartbeat_text = (fs.workspace_path / "HEARTBEAT.md").read_text(encoding="utf-8")
    assert "skills/moltbook/HEARTBEAT.md" not in workspace_heartbeat_text
    assert "skills/moltbook/SKILL.md" not in workspace_heartbeat_text
    assert workspace_heartbeat_text.strip() == "# HEARTBEAT.md"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_agent_directory_fails_when_required_environment_url_value_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    runtimes_root = tmp_path / "runtimes"
    environments_root = tmp_path / "environments"
    _seed_runtime_package(runtimes_root)
    _seed_public_environment(environments_root)

    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(agent_fs_module, "RUNTIME_PACKAGE_PATH", runtimes_root)
    monkeypatch.setattr(agent_fs_module, "ENVIRONMENT_PACKAGE_PATH", environments_root)

    fs = AgentFilesystem("agent-2", run_id="run-1")
    result = await fs.create_agent_directory(
        runtime_id="openclaw",
        agent_name="agent-name-2",
        environment_url=None,
        environment_name="moltbook",
    )

    assert result["created"] is False
    assert "Missing required config placeholder values: ENVIRONMENT_URL" in str(result.get("error"))
    assert fs.agent_path.exists() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bootstrap_environment_skill_fetches_from_environment_url_when_local_package_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    runtimes_root = tmp_path / "runtimes"
    _seed_runtime_package(runtimes_root)

    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(agent_fs_module, "RUNTIME_PACKAGE_PATH", runtimes_root)

    async def _fake_fetch(self, environment_url: str, skill_name: str, filename: str):
        if filename == "SKILL.md":
            return "# Remote Skill\nRead HEARTBEAT.md, MESSAGING.md, and RULES.md.\n"
        if filename == "HEARTBEAT.md":
            return "# Remote Heartbeat\n"
        if filename == "MESSAGING.md":
            return "# Remote Messaging\n"
        if filename == "RULES.md":
            return "# Remote Rules\n"
        return None

    monkeypatch.setattr(AgentFilesystem, "_fetch_skill_file", _fake_fetch)

    fs = AgentFilesystem("agent-3", run_id="run-1")
    fs.agent_path.mkdir(parents=True, exist_ok=True)
    fs.skills_path.mkdir(parents=True, exist_ok=True)

    result = await fs.bootstrap_environment_skill(
        environment_name="moltbook",
        environment_url="http://environment:8000",
    )

    assert result["success"] is True
    assert result["source_file"] == "http://environment:8000/skill.md"
    assert (
        fs.skills_path / "moltbook" / "SKILL.md"
    ).read_text(encoding="utf-8") == "# Remote Skill\nRead HEARTBEAT.md, MESSAGING.md, and RULES.md.\n"
    assert (fs.skills_path / "moltbook" / "HEARTBEAT.md").read_text(encoding="utf-8") == "# Remote Heartbeat\n"
    assert (fs.skills_path / "moltbook" / "MESSAGING.md").read_text(encoding="utf-8") == "# Remote Messaging\n"
    assert (fs.skills_path / "moltbook" / "RULES.md").read_text(encoding="utf-8") == "# Remote Rules\n"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bootstrap_environment_skill_prefers_public_environment_package_over_remote_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    runtimes_root = tmp_path / "runtimes"
    public_environments_root = tmp_path / "environments"
    _seed_runtime_package(runtimes_root)
    _seed_public_environment(public_environments_root, name="moltbook")

    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(agent_fs_module, "RUNTIME_PACKAGE_PATH", runtimes_root)
    monkeypatch.setattr(agent_fs_module, "ENVIRONMENT_PACKAGE_PATH", public_environments_root)

    fs = AgentFilesystem("agent-4", run_id="run-1")
    fs.agent_path.mkdir(parents=True, exist_ok=True)
    fs.skills_path.mkdir(parents=True, exist_ok=True)

    result = await fs.bootstrap_environment_skill(
        environment_name="moltbook",
        environment_url="http://environment:8000",
    )

    assert result["success"] is True
    assert result["source_file"] == str(public_environments_root / "moltbook" / "skill.md")
    assert (fs.skills_path / "moltbook" / "SKILL.md").read_text(encoding="utf-8") == (
        "# Public Skill\nSee HEARTBEAT.md and RULES.md.\n"
    )


@pytest.mark.unit
def test_create_agent_request_uses_runtime_fields_only() -> None:
    request = launcher.CreateAgentRequest.model_validate(
        {
            "agent_id": "agent-runtime",
            "run_id": "run-1",
            "runtime_id": "openclaw",
            "runtime_content_hash": "sha256:abc123",
        }
    )

    assert request.runtime_id == "openclaw"
    assert request.runtime_content_hash == "sha256:abc123"
