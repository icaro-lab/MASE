"""Unit tests for prompt budgeting and truncation metadata."""

from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app.agent_fs import AgentFilesystem
from app.config import settings


async def _build_agent_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    heartbeat_content: str,
    skill_content: str,
) -> AgentFilesystem:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    agent_fs = AgentFilesystem("agent-budget")
    agent_fs.workspace_path.mkdir(parents=True, exist_ok=True)
    agent_fs.skills_path.mkdir(parents=True, exist_ok=True)

    await agent_fs.write_file("AGENTS.md", "# AGENTS\nRules\n")
    await agent_fs.write_file("IDENTITY.md", "# IDENTITY\nPersona\n")
    await agent_fs.write_file("SOUL.md", "# SOUL\nValues\n")
    await agent_fs.write_file("TOOLS.md", "# TOOLS\nUse tools\n")
    await agent_fs.write_file("BOOTSTRAP.md", "# BOOTSTRAP\nInit\n")
    await agent_fs.write_file("USER.md", "# USER\nNo user\n")
    await agent_fs.write_file("HEARTBEAT.md", heartbeat_content)

    skill_dir = agent_fs.skills_path / "moltbook"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
    (skill_dir / "HEARTBEAT.md").write_text("# Skill HB\nDo actions\n", encoding="utf-8")

    return agent_fs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_tail_budget_keeps_recent_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [f"- entry-{idx}" for idx in range(200)]
    heartbeat_content = "\n".join(entries)
    agent_fs = await _build_agent_workspace(
        tmp_path,
        monkeypatch,
        heartbeat_content=heartbeat_content,
        skill_content="# Skill\nDo thing\n",
    )

    prompt, parts = await agent_fs.assemble_system_prompt_with_parts(
        heartbeat_tail_chars=280,
    )

    assert "[TRUNCATED HEARTBEAT.md; showing tail]" in prompt
    assert "entry-199" in prompt
    assert "entry-0" not in prompt
    hb_part = next(part for part in parts if part.get("name") == "HEARTBEAT.md")
    assert hb_part["truncated"] is True
    assert hb_part["truncation_reason"] == "heartbeat_tail_budget"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prompt_budget_applies_max_prompt_chars_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    huge_skill = "# Skill\n" + ("x" * 6000)
    agent_fs = await _build_agent_workspace(
        tmp_path,
        monkeypatch,
        heartbeat_content="Heartbeat line\n" * 30,
        skill_content=huge_skill,
    )

    prompt, parts = await agent_fs.assemble_system_prompt_with_parts(
        max_skill_chars=5000,
        max_prompt_chars=800,
    )

    assert len(prompt) <= 800
    budget_part = next(part for part in parts if part.get("kind") == "prompt_budget")
    assert budget_part["truncated"] is True
    assert budget_part["truncation_reason"] == "max_prompt_chars"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_available_skills_budget_truncates_metadata_block_and_reports_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_fs = await _build_agent_workspace(
        tmp_path,
        monkeypatch,
        heartbeat_content="HB\n",
        skill_content="# moltbook\nPrimary skill\n",
    )
    for name in ("alpha", "beta", "gamma", "delta"):
        skill_dir = agent_fs.skills_path / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n{name} description\n", encoding="utf-8")

    _prompt, parts = await agent_fs.assemble_system_prompt_with_parts(
        max_skill_chars=240,
    )

    skill_part = next(part for part in parts if part.get("name") == "Skills")
    assert skill_part["truncated"] is True
    assert skill_part["truncation_reason"] in {"skills_char_budget", "skills_count_budget"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prompt_parts_manifest_includes_truncation_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [f"- line-{idx}" for idx in range(150)]
    agent_fs = await _build_agent_workspace(
        tmp_path,
        monkeypatch,
        heartbeat_content="\n".join(entries),
        skill_content="# Skill\n" + ("z" * 5000),
    )
    for name in ("alpha", "beta", "gamma", "delta"):
        skill_dir = agent_fs.skills_path / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n{name} description\n", encoding="utf-8")

    _prompt, parts = await agent_fs.assemble_system_prompt_with_parts(
        heartbeat_tail_chars=200,
        max_skill_chars=240,
        max_prompt_chars=420,
    )

    truncated_parts = [part for part in parts if part.get("truncated")]
    reasons = {part.get("truncation_reason") for part in truncated_parts}

    assert "heartbeat_tail_budget" in reasons
    assert "skills_char_budget" in reasons or "skills_count_budget" in reasons
    assert "max_prompt_chars" in reasons
