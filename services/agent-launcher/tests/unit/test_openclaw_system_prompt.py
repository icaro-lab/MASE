"""Unit tests for Python OpenClaw system prompt assembly."""

from pathlib import Path

import pytest

from app.agent_fs import AgentFilesystem
from app.config import settings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_system_prompt_uses_openclaw_section_order_and_project_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_fs = AgentFilesystem("agent-openclaw")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    agent_fs.skills_path.mkdir(parents=True, exist_ok=True)

    for filename, content in (
        ("AGENTS.md", "# AGENTS\nRules\n"),
        ("IDENTITY.md", "# IDENTITY\nPersona\n"),
        ("SOUL.md", "# SOUL\nVoice\n"),
        ("TOOLS.md", "# TOOLS\nTool notes\n"),
        ("BOOTSTRAP.md", "# BOOTSTRAP\nInit\n"),
        ("USER.md", "# USER\nNone\n"),
        ("HEARTBEAT.md", "# HEARTBEAT\nCheck status\n"),
    ):
        await agent_fs.write_file(filename, content)

    skill_dir = agent_fs.skills_path / "moltbook"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# moltbook\nsocial environment\n", encoding="utf-8")

    prompt, parts = await agent_fs.assemble_system_prompt_with_parts(
        runtime_context={
            "interaction_mode": "heartbeat_autonomous",
            "heartbeat_prompt": "Read HEARTBEAT.md and act or HEARTBEAT_OK.",
        }
    )

    assert "## Tool Call Style" in prompt
    assert "## Safety" in prompt
    assert "## Skills (mandatory)" in prompt
    assert "<available_skills>" in prompt
    assert "# Project Context" in prompt
    assert "# AGENTS" in prompt
    assert "# HEARTBEAT" in prompt
    assert "Heartbeat prompt: Read HEARTBEAT.md and act or HEARTBEAT_OK." in prompt
    assert "do not just reply HEARTBEAT_OK every time" in prompt
    assert "Use `HEARTBEAT.md` as a tiny checklist of things worth checking periodically." in prompt

    part_names = [part["name"] for part in parts]
    assert part_names[:6] == [
        "Tool Call Style",
        "Safety",
        "Skills",
        "Heartbeats",
        "Project Context",
        "AGENTS.md",
    ]
