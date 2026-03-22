"""Unit tests for mutable skill-local state writes in ActionExecutor."""

from pathlib import Path

import pytest

from app.action_parser import Action, ActionType
from app.agent_fs import AgentFilesystem
from app.config import settings
from app.executor import ActionExecutor


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_state_write_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    fs = AgentFilesystem("agent-1")
    fs.agent_path.mkdir(parents=True, exist_ok=True)
    fs.workspace_path.mkdir(parents=True, exist_ok=True)
    fs.skills_path.mkdir(parents=True, exist_ok=True)
    (fs.skills_path / "moltbook" / "state").mkdir(parents=True, exist_ok=True)

    executor = ActionExecutor(fs, agent_id="agent-1", run_id="run-1")
    result = await executor.execute_action(
        Action(
            action=ActionType.FILESYSTEM,
            operation="write",
            path="skills/moltbook/state/credentials.json",
            content='{"api_token":"token"}',
        )
    )

    assert result["success"] is True
    assert (fs.skills_path / "moltbook" / "state" / "credentials.json").read_text(encoding="utf-8") == '{"api_token":"token"}'
    await executor.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skill_doc_write_remains_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    fs = AgentFilesystem("agent-1")
    fs.agent_path.mkdir(parents=True, exist_ok=True)
    fs.workspace_path.mkdir(parents=True, exist_ok=True)
    fs.skills_path.mkdir(parents=True, exist_ok=True)
    skill_dir = fs.skills_path / "moltbook"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# Moltbook\n", encoding="utf-8")

    executor = ActionExecutor(fs, agent_id="agent-1", run_id="run-1")
    result = await executor.execute_action(
        Action(
            action=ActionType.FILESYSTEM,
            operation="write",
            path="skills/moltbook/SKILL.md",
            content="# Replaced\n",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "fs_path_outside_workspace"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Moltbook\n"
    await executor.close()
