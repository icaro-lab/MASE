"""Unit tests for direct OpenClaw-style tool-call parsing and execution."""

from pathlib import Path

import pytest

from app.action_parser import ActionType, parse_multiple_actions
from app.agent_fs import AgentFilesystem
from app.config import settings
from app.executor import ActionExecutor


@pytest.mark.unit
def test_parse_multiple_actions_supports_edit_and_delete_direct_calls() -> None:
    edit_actions = parse_multiple_actions('edit({"path":"notes.txt","find":"old","replace":"new"})')
    delete_actions = parse_multiple_actions('delete({"path":"notes.txt"})')

    assert len(edit_actions) == 1
    assert edit_actions[0].action == ActionType.FILESYSTEM
    assert edit_actions[0].operation == "edit"
    assert edit_actions[0].find == "old"
    assert edit_actions[0].replace == "new"

    assert len(delete_actions) == 1
    assert delete_actions[0].action == ActionType.FILESYSTEM
    assert delete_actions[0].operation == "delete"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_supports_edit_and_delete_filesystem_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agents_base_path", str(tmp_path / "agents"))

    fs = AgentFilesystem("agent-edit")
    fs.agent_path.mkdir(parents=True, exist_ok=True)
    fs.workspace_path.mkdir(parents=True, exist_ok=True)
    fs.skills_path.mkdir(parents=True, exist_ok=True)

    await fs.write_file("notes.txt", "old value\n")

    executor = ActionExecutor(fs, agent_id="agent-edit", run_id="run-1")

    edit_action = parse_multiple_actions(
        'edit({"path":"notes.txt","find":"old","replace":"new"})'
    )[0]
    delete_action = parse_multiple_actions('delete({"path":"notes.txt"})')[0]

    edit_result = await executor.execute_action(edit_action)
    assert edit_result["success"] is True
    assert edit_result["operation"] == "edit"
    assert await fs.read_file("notes.txt") == "new value\n"

    delete_result = await executor.execute_action(delete_action)
    assert delete_result["success"] is True
    assert delete_result["operation"] == "delete"
    assert await fs.file_exists("notes.txt") is False

    await executor.close()
