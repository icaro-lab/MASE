"""Unit tests for bounded hidden heartbeat runtime log behavior."""

import asyncio
from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app import agent_fs as agent_fs_module
from app.agent_fs import AgentFilesystem
from app.config import settings


@pytest.mark.unit
def test_update_heartbeat_keeps_prefix_and_caps_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(agent_fs_module, "HEARTBEAT_LOG_MAX_ENTRIES", 3)
    monkeypatch.setattr(agent_fs_module, "HEARTBEAT_LOG_MAX_CHARS", 20000)

    agent_fs = AgentFilesystem("agent-heartbeat-cap")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    asyncio.run(agent_fs.write_file("HEARTBEAT.md", "# HEARTBEAT\nBase instructions\n"))

    for idx in range(5):
        asyncio.run(agent_fs.update_heartbeat("completed", f"summary-{idx}"))

    heartbeat_md = asyncio.run(agent_fs.read_file("HEARTBEAT.md"))
    runtime_log = (agent_fs.workspace_path / agent_fs_module.HEARTBEAT_RUNTIME_LOG_FILENAME).read_text(
        encoding="utf-8"
    )
    assert heartbeat_md is not None
    assert heartbeat_md.strip() == "# HEARTBEAT\nBase instructions"
    assert agent_fs_module.HEARTBEAT_LOG_SECTION_HEADER not in heartbeat_md
    assert runtime_log.count("## Heartbeat Update") == 3
    assert "summary-4" in runtime_log
    assert "summary-3" in runtime_log
    assert "summary-2" in runtime_log
    assert "summary-0" not in runtime_log
    assert "summary-1" not in runtime_log


@pytest.mark.unit
def test_update_heartbeat_enforces_char_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(agent_fs_module, "HEARTBEAT_LOG_MAX_ENTRIES", 120)
    monkeypatch.setattr(agent_fs_module, "HEARTBEAT_LOG_MAX_CHARS", 900)

    agent_fs = AgentFilesystem("agent-heartbeat-size")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    asyncio.run(agent_fs.write_file("HEARTBEAT.md", "# HEARTBEAT\nBase instructions\n"))

    for idx in range(20):
        asyncio.run(agent_fs.update_heartbeat("completed", f"very-long-summary-{idx}-" + ("x" * 120)))

    runtime_log = (agent_fs.workspace_path / agent_fs_module.HEARTBEAT_RUNTIME_LOG_FILENAME).read_text(
        encoding="utf-8"
    )
    assert len(runtime_log) <= 900
    assert "very-long-summary-19" in runtime_log


@pytest.mark.unit
def test_get_recent_heartbeat_entries_returns_tail_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(agent_fs_module, "HEARTBEAT_LOG_MAX_ENTRIES", 120)
    monkeypatch.setattr(agent_fs_module, "HEARTBEAT_LOG_MAX_CHARS", 20000)

    agent_fs = AgentFilesystem("agent-heartbeat-tail")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    asyncio.run(agent_fs.write_file("HEARTBEAT.md", "# HEARTBEAT\nBase instructions\n"))

    for idx in range(6):
        asyncio.run(agent_fs.update_heartbeat("completed", f"summary-{idx}"))

    recent = asyncio.run(agent_fs.get_recent_heartbeat_entries(3))
    assert len(recent) == 3
    assert "summary-3" in recent[0]
    assert "summary-4" in recent[1]
    assert "summary-5" in recent[2]


@pytest.mark.unit
def test_assemble_system_prompt_ignores_legacy_heartbeat_log_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_fs = AgentFilesystem("agent-heartbeat-prompt")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    asyncio.run(
        agent_fs.write_file(
            "HEARTBEAT.md",
            "# HEARTBEAT\nBase instructions\n\n## Runtime Heartbeat Log\n\n## Heartbeat Update\n\n- **Summary**: old\n---\n\n",
        )
    )

    prompt, _parts = asyncio.run(agent_fs.assemble_system_prompt_with_parts())

    assert "Base instructions" in prompt
    assert "Runtime Heartbeat Log" not in prompt
    assert "**Summary**: old" not in prompt
