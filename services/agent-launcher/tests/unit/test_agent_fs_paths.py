"""Unit tests for agent workspace path normalization."""

from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app.agent_fs import AgentFilesystem
from app.config import settings


@pytest.mark.unit
def test_resolve_file_path_strips_workspace_and_agent_prefixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_id = "agent-3"
    agent_fs = AgentFilesystem(agent_id)
    expected = agent_fs.workspace_path / "HEARTBEAT.md"

    candidates = [
        "HEARTBEAT.md",
        "/HEARTBEAT.md",
        "workspace/HEARTBEAT.md",
        "/workspace/HEARTBEAT.md",
        f"/agents/{agent_id}/workspace/HEARTBEAT.md",
        f"/agents/{agent_id}/workspace/workspace/HEARTBEAT.md",
        f"/agents/{agent_id}/workspace/agents/{agent_id}/workspace/HEARTBEAT.md",
        f"{agent_id}/workspace/HEARTBEAT.md",
    ]

    for candidate in candidates:
        assert agent_fs._resolve_file_path(candidate) == expected


@pytest.mark.unit
def test_resolve_file_path_keeps_relative_subpaths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_id = "agent-7"
    agent_fs = AgentFilesystem(agent_id)

    resolved = agent_fs._resolve_file_path(f"/agents/{agent_id}/workspace/notes/tick-01.md")
    assert resolved == agent_fs.workspace_path / "notes" / "tick-01.md"


@pytest.mark.unit
def test_resolve_file_path_supports_skills_root_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_id = "agent-9"
    agent_fs = AgentFilesystem(agent_id)
    expected = agent_fs.skills_path / "moltbook" / "SKILL.md"

    candidates = [
        "skills/moltbook/SKILL.md",
        "/skills/moltbook/SKILL.md",
        f"/agents/{agent_id}/skills/moltbook/SKILL.md",
        f"{agent_id}/skills/moltbook/SKILL.md",
    ]
    for candidate in candidates:
        assert agent_fs._resolve_file_path(candidate) == expected
