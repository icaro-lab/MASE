"""Unit tests for available-skills metadata prompt contract."""

from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app.agent_fs import AgentFilesystem


async def _seed_skill(skills_root: Path, skill_name: str) -> None:
    skill_dir = skills_root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"# {skill_name}\n{skill_name} skill description\n",
        encoding="utf-8",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_available_skills_lists_installed_skills_metadata(tmp_path: Path) -> None:
    fs = AgentFilesystem("agent-test")
    fs.skills_path = tmp_path / "skills"

    await _seed_skill(fs.skills_path, "alpha")
    await _seed_skill(fs.skills_path, "beta")

    content, stats = await fs.build_available_skills_prompt(
        max_skills_in_prompt=10,
        max_skills_prompt_chars=4000,
    )

    assert "<available_skills>" in content
    assert "<name>alpha</name>" in content
    assert "<name>beta</name>" in content
    assert "<location>skills/alpha/SKILL.md</location>" in content
    assert "<location>skills/beta/SKILL.md</location>" in content
    assert stats["skills_total"] == 2
    assert stats["skills_count"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_available_skills_prefers_frontmatter_description(tmp_path: Path) -> None:
    fs = AgentFilesystem("agent-test")
    fs.skills_path = tmp_path / "skills"

    skill_dir = fs.skills_path / "moltbook"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: moltbook\ndescription: Social network for AI agents\n---\n\n# Moltbook\nBody\n",
        encoding="utf-8",
    )

    content, _stats = await fs.build_available_skills_prompt(
        max_skills_in_prompt=10,
        max_skills_prompt_chars=4000,
    )

    assert "<description>Social network for AI agents</description>" in content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_available_skills_allowlist_filters_metadata(tmp_path: Path) -> None:
    fs = AgentFilesystem("agent-test")
    fs.skills_path = tmp_path / "skills"

    await _seed_skill(fs.skills_path, "alpha")
    await _seed_skill(fs.skills_path, "zeta")

    content, stats = await fs.build_available_skills_prompt(
        skill_allowlist=["zeta", "missing"],
        max_skills_in_prompt=10,
        max_skills_prompt_chars=4000,
    )

    assert "<name>zeta</name>" in content
    assert "<name>alpha</name>" not in content
    assert stats["skills_total"] == 1
    assert stats["skills_count"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_skill_mode_flag_is_compatibility_only_for_prompt_assembly(
    tmp_path: Path,
) -> None:
    fs = AgentFilesystem("agent-test")
    fs.workspace_path = tmp_path / "workspace"
    fs.skills_path = tmp_path / "skills"
    fs.workspace_path.mkdir(parents=True, exist_ok=True)

    for filename in ("AGENTS.md", "IDENTITY.md", "SOUL.md", "TOOLS.md", "BOOTSTRAP.md", "USER.md", "HEARTBEAT.md"):
        await fs.write_file(filename, f"# {filename}\n")

    await _seed_skill(fs.skills_path, "alpha")
    await _seed_skill(fs.skills_path, "beta")

    _, parts_single = await fs.assemble_system_prompt_with_parts(
        single_skill_mode=True,
        preferred_skill_name="beta",
    )
    _, parts_multi = await fs.assemble_system_prompt_with_parts(
        single_skill_mode=False,
        preferred_skill_name="beta",
    )

    skill_part_single = next(part for part in parts_single if part.get("kind") == "available_skills")
    skill_part_multi = next(part for part in parts_multi if part.get("kind") == "available_skills")

    assert skill_part_single["skills_count"] == skill_part_multi["skills_count"] == 2
