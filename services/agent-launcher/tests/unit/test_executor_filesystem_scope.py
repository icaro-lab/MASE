"""Unit tests for filesystem scope rules in ActionExecutor."""

import types
from pathlib import Path
import sys

import pytest


from _bootstrap import PROJECT_ROOT


# Keep unit tests independent from optional apscheduler dependency.
if "apscheduler.schedulers.asyncio" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    apscheduler_schedulers_module = types.ModuleType("apscheduler.schedulers")
    apscheduler_asyncio_module = types.ModuleType("apscheduler.schedulers.asyncio")
    apscheduler_triggers_module = types.ModuleType("apscheduler.triggers")
    apscheduler_date_module = types.ModuleType("apscheduler.triggers.date")

    class _DummyAsyncIOScheduler:
        def start(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            return None

        def add_job(self, *args, **kwargs) -> None:
            return None

        def remove_job(self, *args, **kwargs) -> None:
            return None

    class _DummyDateTrigger:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    apscheduler_asyncio_module.AsyncIOScheduler = _DummyAsyncIOScheduler
    apscheduler_date_module.DateTrigger = _DummyDateTrigger

    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.schedulers"] = apscheduler_schedulers_module
    sys.modules["apscheduler.schedulers.asyncio"] = apscheduler_asyncio_module
    sys.modules["apscheduler.triggers"] = apscheduler_triggers_module
    sys.modules["apscheduler.triggers.date"] = apscheduler_date_module

from app.action_parser import Action, ActionType
from app.agent_fs import AgentFilesystem
from app.executor import ActionExecutor
from app.config import settings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filesystem_read_can_access_installed_skill_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_fs = AgentFilesystem("agent-1")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    agent_fs.skills_path.mkdir(parents=True, exist_ok=True)
    skill_dir = agent_fs.skills_path / "moltbook"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# Skill\nDo stuff\n", encoding="utf-8")

    executor = ActionExecutor(agent_fs, agent_id="agent-1", run_id="run-1")
    try:
        result = await executor.execute_action(
            Action(
                action=ActionType.FILESYSTEM,
                action_name="read_skill",
                operation="read",
                path="skills/moltbook/SKILL.md",
            )
        )
    finally:
        await executor.close()

    assert result["success"] is True
    assert "Do stuff" in str(result.get("content") or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filesystem_write_cannot_mutate_skills_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_fs = AgentFilesystem("agent-1")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    agent_fs.skills_path.mkdir(parents=True, exist_ok=True)

    executor = ActionExecutor(agent_fs, agent_id="agent-1", run_id="run-1")
    try:
        result = await executor.execute_action(
            Action(
                action=ActionType.FILESYSTEM,
                action_name="write_skill",
                operation="write",
                path="skills/moltbook/SKILL.md",
                content="malicious overwrite",
            )
        )
    finally:
        await executor.close()

    assert result["success"] is False
    assert result["error_code"] == "fs_path_outside_workspace"

