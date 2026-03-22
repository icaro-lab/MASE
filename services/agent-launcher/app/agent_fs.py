"""Agent filesystem management module."""

import os
import json
import aiofiles
import httpx
import hashlib
import shutil
import re
from html import escape as xml_escape
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from .config import settings
from .openclaw_core.system_prompt import (
    ContextFile,
    build_openclaw_system_prompt_parts,
)


# Public runtime/environment asset paths
ENVIRONMENT_PACKAGE_PATH = Path("/app/environments")
RUNTIME_PACKAGE_PATH = Path("/app/runtimes")
IGNORED_PACKAGE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    ".svelte-kit",
    "build",
    "dist",
    ".venv",
    "venv",
}


def _read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    """Read positive integer env var with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    if value < minimum:
        return default
    return value


HEARTBEAT_LOG_SECTION_HEADER = "## Runtime Heartbeat Log"
HEARTBEAT_RUNTIME_LOG_FILENAME = ".heartbeat-log.md"
HEARTBEAT_SESSION_FILENAME = ".heartbeat-session.json"
HEARTBEAT_LOG_MAX_ENTRIES = _read_positive_int_env("AGENT_HEARTBEAT_LOG_MAX_ENTRIES", 120)
HEARTBEAT_LOG_MAX_CHARS = _read_positive_int_env("AGENT_HEARTBEAT_LOG_MAX_CHARS", 120000, minimum=4096)
SKILLS_PROMPT_MAX_ITEMS_DEFAULT = _read_positive_int_env("AGENT_SKILLS_PROMPT_MAX_ITEMS", 150)
SKILLS_PROMPT_MAX_CHARS_DEFAULT = _read_positive_int_env(
    "AGENT_SKILLS_PROMPT_MAX_CHARS",
    30000,
    minimum=512,
)
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")
SKILL_DOC_LINK_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_-]*\.md)\b")


def _split_heartbeat_log(content: str) -> Tuple[str, List[str]]:
    """Split HEARTBEAT.md into static prefix and runtime log entries."""
    text = str(content or "")
    marker = f"\n\n{HEARTBEAT_LOG_SECTION_HEADER}\n\n"
    prefix = ""
    raw_log = ""

    if marker in text:
        prefix, raw_log = text.split(marker, 1)
    elif "## Heartbeat Update" in text:
        # Legacy format before dedicated runtime-log section.
        marker_index = text.find("## Heartbeat Update")
        prefix = text[:marker_index]
        raw_log = text[marker_index:]
    else:
        prefix = text

    entries: List[str] = []
    for chunk in raw_log.split("\n---\n\n"):
        item = chunk.strip()
        if item:
            entries.append(item)
    return prefix.rstrip(), entries


def _compose_heartbeat_log(prefix: str, entries: List[str]) -> str:
    """Compose HEARTBEAT.md from static prefix + runtime log entries."""
    prefix_text = str(prefix or "").rstrip()
    if not entries:
        return f"{prefix_text}\n" if prefix_text else ""

    body = "\n---\n\n".join(item.strip() for item in entries if item.strip())
    if not body:
        return f"{prefix_text}\n" if prefix_text else ""

    if prefix_text:
        return f"{prefix_text}\n\n{HEARTBEAT_LOG_SECTION_HEADER}\n\n{body}\n---\n\n"
    return f"{HEARTBEAT_LOG_SECTION_HEADER}\n\n{body}\n---\n\n"


def normalize_environment_name(environment_name: Optional[str]) -> str:
    """Normalize environment refs to bare name (e.g. `myenv`)."""
    if not environment_name:
        return ""

    cleaned = environment_name.strip().strip("/")
    if cleaned.startswith("environments/"):
        cleaned = cleaned[len("environments/"):]

    return Path(cleaned).name or cleaned


def _canonical_skill_doc_name(path: Path) -> str:
    return f"{path.stem.upper()}.md"


def _linked_skill_doc_filenames(skill_content: Optional[str]) -> List[str]:
    linked: List[str] = []
    for match in SKILL_DOC_LINK_PATTERN.findall(str(skill_content or "")):
        normalized = match.strip()
        if normalized == "SKILL.md":
            continue
        if normalized not in linked:
            linked.append(normalized)
    return linked


def normalize_agent_runtime_name(runtime_id: Optional[str]) -> str:
    """Normalize runtime refs to bare runtime name (e.g. `openclaw`)."""
    if not runtime_id:
        return "openclaw"

    cleaned = runtime_id.strip().strip("/")
    if cleaned.startswith("runtime/"):
        cleaned = cleaned[len("runtime/"):]
    if cleaned.startswith("runtimes/"):
        cleaned = cleaned[len("runtimes/"):]

    return Path(cleaned).name or "openclaw"


def _iter_markdown_files_in_snapshot(snapshot_root: Path) -> List[Path]:
    files: List[Path] = []
    if not snapshot_root.exists() or not snapshot_root.is_dir():
        return files
    for path in snapshot_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(snapshot_root)
        if any(part in IGNORED_PACKAGE_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() != ".md":
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(snapshot_root).as_posix())


def _iter_snapshot_hash_files(snapshot_root: Path) -> List[Path]:
    files = list(_iter_markdown_files_in_snapshot(snapshot_root))
    config_path = snapshot_root / "config.json"
    if config_path.exists() and config_path.is_file():
        files.append(config_path)
    return sorted(files, key=lambda item: item.relative_to(snapshot_root).as_posix())


def _sha256_snapshot_tree(snapshot_root: Path) -> str:
    hasher = hashlib.sha256()
    for file_path in _iter_snapshot_hash_files(snapshot_root):
        rel = file_path.relative_to(snapshot_root).as_posix().encode("utf-8")
        hasher.update(rel)
        hasher.update(b"\0")
        hasher.update(file_path.read_bytes())
        hasher.update(b"\0")
    return f"sha256:{hasher.hexdigest()}"


def _truncate_text(
    text: str,
    *,
    max_chars: Optional[int],
    prefer_tail: bool = False,
    marker_label: str = "section",
) -> Tuple[str, bool]:
    """Truncate text with a short marker when max_chars is exceeded."""
    if not max_chars or max_chars <= 0:
        return text, False
    if len(text) <= max_chars:
        return text, False

    marker = f"[TRUNCATED {marker_label}; showing {'tail' if prefer_tail else 'head'}]\n"
    available = max(max_chars - len(marker), 0)
    if available <= 0:
        return marker[:max_chars], True

    if prefer_tail:
        return marker + text[-available:], True
    return text[:available] + marker, True


def _first_nonempty_line(text: str) -> str:
    """Return first non-empty line without markdown heading markers."""
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        while line.startswith("#"):
            line = line[1:].strip()
        if line:
            return line
    return ""


def _extract_frontmatter_value(text: str, key: str) -> Optional[str]:
    """Extract a simple scalar value from leading YAML frontmatter."""
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    for raw_line in lines[1:]:
        line = raw_line.rstrip()
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        if field.strip().lower() != key.strip().lower():
            continue
        normalized = value.strip().strip('"').strip("'")
        return normalized or None
    return None


def _strip_frontmatter(text: str) -> str:
    """Remove leading YAML frontmatter if present."""
    raw = str(text or "")
    if not raw.startswith("---"):
        return raw

    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return raw

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return raw


def _skill_prompt_description(text: str, skill_name: str) -> str:
    """Resolve a useful skill description for <available_skills> metadata."""
    frontmatter_description = _extract_frontmatter_value(text, "description")
    if frontmatter_description:
        return frontmatter_description

    body = _strip_frontmatter(text)
    first_line = _first_nonempty_line(body)
    if first_line:
        return first_line

    return f"Skill instructions for {skill_name}"


def _render_placeholders(
    text: str,
    values: Dict[str, str],
    *,
    replace_missing: bool,
) -> Tuple[str, List[str]]:
    """Render {{TOKEN}} placeholders with optional missing-token replacement."""
    unresolved: List[str] = []

    def _replace(match: re.Match[str]) -> str:
        token = str(match.group(1) or "").strip()
        value = values.get(token)
        if value is None:
            unresolved.append(token)
            return "" if replace_missing else match.group(0)
        return str(value)

    rendered = PLACEHOLDER_PATTERN.sub(_replace, str(text or ""))
    # Preserve stable order while deduplicating.
    seen: List[str] = []
    for token in unresolved:
        if token not in seen:
            seen.append(token)
    return rendered, seen


def get_environment_skill_path(environment_name: str, institutional_mode: bool = False) -> Optional[Path]:
    """Get the path to the environment skill file.
    
    Args:
        environment_name: Name of the environment (e.g., "myenv")
        institutional_mode: Whether to use institutional mode skill file
        
    Returns:
        Path to the skill file or None if not found
    """
    normalized_env = normalize_environment_name(environment_name)
    if not normalized_env:
        return None
    
    skill_filename = "inst_skill.md" if institutional_mode else "skill.md"

    skill_path = ENVIRONMENT_PACKAGE_PATH / normalized_env / skill_filename
    if skill_path.exists():
        return skill_path

    if institutional_mode:
        fallback_path = ENVIRONMENT_PACKAGE_PATH / normalized_env / "skill.md"
        if fallback_path.exists():
            return fallback_path

    return None


# Standard agent files in order of assembly (new OpenClaw-base structure)
STANDARD_FILES = [
    "AGENTS.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "BOOTSTRAP.md",
    "USER.md",
    "HEARTBEAT.md",
]


class AgentFilesystem:
    """Manages agent filesystem operations."""
    
    def __init__(self, agent_id: str, run_id: str = "default"):
        self.agent_id = agent_id
        self.run_id = run_id
        # agent_id is unique within the run workspace.
        self.agent_path = Path(settings.agents_base_path) / agent_id
        # New structure: workspace/ subdirectory for markdown files
        self.workspace_path = self.agent_path / "workspace"
        self.skills_path = self.agent_path / "skills"
    
    async def exists(self) -> bool:
        """Check if agent directory exists."""
        return await self._async_path_exists(self.agent_path)

    def _heartbeat_runtime_log_path(self) -> Path:
        """Path for runtime heartbeat/action log kept outside prompt-visible files."""
        return self.workspace_path / HEARTBEAT_RUNTIME_LOG_FILENAME

    def _heartbeat_session_path(self) -> Path:
        """Path for persisted heartbeat/session transcript state."""
        return self.workspace_path / HEARTBEAT_SESSION_FILENAME

    def _load_local_agent_config(self) -> Dict[str, Any]:
        config_path = self.agent_path / "config.json"
        if not config_path.exists() or not config_path.is_file():
            return {}
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def get_runtime_init_mode(self) -> str:
        runtime_block = self._load_local_agent_config().get("runtime")
        if isinstance(runtime_block, dict):
            value = str(runtime_block.get("init_mode") or "").strip().lower()
            if value:
                return value
        return "manual"
    
    async def _async_path_exists(self, path: Path) -> bool:
        """Check if path exists (async wrapper)."""
        return path.exists()
    
    async def bootstrap_environment_skill(
        self,
        environment_name: str,
        environment_url: Optional[str] = None,
        institutional_mode: bool = False
    ) -> Dict[str, Any]:
        """Bootstrap the environment skill during agent initialization.
        
        Copies the appropriate skill file from environment packages to the
        agent's skills directory.
        
        Args:
            environment_name: Name of the environment (e.g., "myenv")
            institutional_mode: Whether to use institutional mode skill file
            
        Returns:
            Dict with bootstrap result and metadata
        """
        result = {
            "environment": normalize_environment_name(environment_name),
            "institutional_mode": institutional_mode,
            "success": False,
            "skill_path": None,
            "source_file": None,
            "installed_files": [],
            "error": None
        }
        
        normalized_env = normalize_environment_name(environment_name)
        if not normalized_env:
            result["error"] = "No environment name provided"
            return result
        
        source_path = get_environment_skill_path(normalized_env, institutional_mode)
        dest_dir = self.skills_path / normalized_env
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        dest_skill_path = dest_dir / "SKILL.md"
        
        try:
            source_label: Optional[str] = None
            skill_content: Optional[str] = None

            if source_path:
                async with aiofiles.open(source_path, "r") as f:
                    skill_content = await f.read()
                source_label = str(source_path)
            elif environment_url:
                fetched_skill = await self._fetch_skill_file(
                    environment_url=environment_url,
                    skill_name=normalized_env,
                    filename="SKILL.md",
                )
                if fetched_skill:
                    skill_content = fetched_skill
                    source_label = f"{environment_url.rstrip('/')}/skill.md"

            if not skill_content:
                if source_path:
                    result["error"] = f"Environment skill file not readable for {normalized_env}"
                elif environment_url:
                    result["error"] = (
                        f"Environment skill file not found for {normalized_env} "
                        f"(local packages and {environment_url.rstrip('/')}/skill.md)"
                    )
                else:
                    result["error"] = (
                        f"Environment skill file not found for {normalized_env} "
                        "(local packages) and no environment_url provided"
                    )
                return result

            async with aiofiles.open(dest_skill_path, 'w') as f:
                await f.write(skill_content)
            
            result["success"] = True
            result["skill_path"] = str(dest_skill_path)
            result["source_file"] = source_label
            result["installed_files"].append(str(dest_skill_path))
            print(f"[DEBUG] Bootstrapped environment skill: {source_label} -> {dest_skill_path}")

            if source_path:
                env_dir = source_path.parent
                linked_names = set(_linked_skill_doc_filenames(skill_content))
                companions = [
                    src
                    for src in sorted(env_dir.glob("*.md"))
                    if src.name != "skill.md"
                    and src.name != "inst_skill.md"
                    and _canonical_skill_doc_name(src) in linked_names
                ]
                for src in companions:
                    if not src.exists():
                        continue
                    dst = dest_dir / _canonical_skill_doc_name(src)
                    async with aiofiles.open(src, "r") as f:
                        content = await f.read()
                    async with aiofiles.open(dst, "w") as f:
                        await f.write(content)
                    result["installed_files"].append(str(dst))
                    print(f"[DEBUG] Bootstrapped environment skill doc: {src} -> {dst}")
            elif environment_url:
                for filename in _linked_skill_doc_filenames(skill_content):
                    dst = dest_dir / filename
                    companion_content = await self._fetch_skill_file(
                        environment_url=environment_url,
                        skill_name=normalized_env,
                        filename=filename,
                    )
                    if not companion_content:
                        continue
                    async with aiofiles.open(dst, "w") as f:
                        await f.write(companion_content)
                    result["installed_files"].append(str(dst))
                    print(
                        f"[DEBUG] Bootstrapped environment skill doc: "
                        f"{environment_url.rstrip('/')}/{filename.lower()} -> {dst}"
                    )
            
        except Exception as e:
            result["error"] = f"Failed to copy skill file: {str(e)}"
            print(f"[ERROR] Failed to bootstrap environment skill: {e}")
        
        return result

    async def seed_workspace_heartbeat_for_environment(self, environment_name: str) -> None:
        """Ensure a generic workspace HEARTBEAT.md exists.

        Environment skills live under `skills/<env>/`. The workspace heartbeat
        should stay generic so the agent can choose a skill, read its SKILL.md
        first, and only then load linked companion docs when needed.
        """
        _ = normalize_environment_name(environment_name)
        existing = (await self.read_file("HEARTBEAT.md") or "").strip()
        if existing:
            return

        seeded = (
            "# HEARTBEAT.md\n\n"
            "Keep this file tiny. Use it as a checklist of things worth checking periodically.\n\n"
            "- if a currently relevant skill is available, read its `SKILL.md` before any linked companion docs, live API checks, or `HEARTBEAT_OK`\n"
            "- unresolved threads or IDs worth revisiting\n"
            "- live-state checks tied to currently relevant skills\n"
            "- short prerequisites or blockers to re-check on a later heartbeat\n\n"
            "If nothing needs attention after checking it, `HEARTBEAT_OK`.\n"
        )
        await self.write_file("HEARTBEAT.md", seeded)
    
    async def validate_bootstrap_reference(self) -> Dict[str, Any]:
        """Validate that BOOTSTRAP.md contains reference to the environment skill.
        
        Returns:
            Dict with validation result and any issues found
        """
        result = {
            "valid": False,
            "bootstrap_exists": False,
            "has_skill_reference": False,
            "warnings": [],
            "fixes_applied": []
        }
        
        bootstrap_path = self.workspace_path / "BOOTSTRAP.md"
        
        if not bootstrap_path.exists():
            if self.get_runtime_init_mode() == "autonomous":
                result["valid"] = True
                return result
            result["warnings"].append("BOOTSTRAP.md not found")
            return result
        
        result["bootstrap_exists"] = True
        
        try:
            async with aiofiles.open(bootstrap_path, 'r') as f:
                content = await f.read()
            
            # Check for skill reference patterns
            skill_patterns = [
                "skills/",
                "SKILL.md",
                "skill.md",
                "Install Required Skills",
                "Skill Discovery"
            ]
            
            has_reference = any(pattern.lower() in content.lower() for pattern in skill_patterns)
            result["has_skill_reference"] = has_reference
            
            if not has_reference:
                result["warnings"].append("BOOTSTRAP.md does not reference skills/{environment}/SKILL.md")
                
                # Auto-fix: Add skill reference note at the end
                skill_note = """

## Environment Skill Reference

This agent has been pre-configured with environment-specific skills.
Review the installed skills in `skills/{environment_name}/SKILL.md`.
"""
                async with aiofiles.open(bootstrap_path, 'a') as f:
                    await f.write(skill_note)
                
                result["fixes_applied"].append("Added environment skill reference to BOOTSTRAP.md")
                result["valid"] = True
            else:
                result["valid"] = True
                
        except Exception as e:
            result["warnings"].append(f"Error reading BOOTSTRAP.md: {str(e)}")
        
        return result

    def _resolve_runtime_materialization(
        self,
        *,
        runtime_id: str,
        runtime_content_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_runtime_id = normalize_agent_runtime_name(runtime_id)
        runtime_base_path = RUNTIME_PACKAGE_PATH / normalized_runtime_id
        if not runtime_base_path.exists() or not runtime_base_path.is_dir():
            raise ValueError(
                f"Agent runtime package not found: {normalized_runtime_id} ({runtime_base_path})"
            )

        expected_hash = str(runtime_content_hash or "").strip() or None

        workspace_source = runtime_base_path / "workspace"
        config_source = runtime_base_path / "config.json"
        source = "current_package"
        resolved_content_hash = _sha256_snapshot_tree(runtime_base_path)
        hash_match = expected_hash == resolved_content_hash if expected_hash else True
        warnings: List[str] = []

        if expected_hash and not hash_match:
            raise ValueError(
                "Agent runtime package content hash mismatch for "
                f"{normalized_runtime_id}: expected={expected_hash}, actual={resolved_content_hash}"
            )

        if not workspace_source.exists() or not workspace_source.is_dir():
            raise ValueError(
                f"Agent runtime workspace not found: {workspace_source}"
            )

        return {
            "runtime_id": normalized_runtime_id,
            "expected_content_hash": expected_hash,
            "resolved_content_hash": resolved_content_hash,
            "hash_match": hash_match,
            "source": source,
            "workspace_source_path": str(workspace_source),
            "config_source_path": str(config_source),
            "warnings": warnings,
        }

    async def create_agent_directory(
        self,
        runtime_id: str = "openclaw",
        runtime_content_hash: Optional[str] = None,
        initial_content: Optional[Dict[str, str]] = None,
        agent_name: Optional[str] = None,
        environment_url: Optional[str] = None,
        environment_name: Optional[str] = None,
        institutional_mode: bool = False
    ) -> Dict[str, Any]:
        """Create agent directory structure with initial files.
        
        New OpenClaw-base structure:
        /agents/{agent_id}/
        ├── config.json                    # Copy from runtime package
        ├── workspace/                     # Copy runtime package workspace/
        │   ├── AGENTS.md
        │   ├── SOUL.md
        │   ├── TOOLS.md
        │   ├── BOOTSTRAP.md
        │   ├── IDENTITY.md
        │   ├── USER.md
        │   └── HEARTBEAT.md
        └── skills/                        # Environment skill + installed skills
            └── {environment_name}/
                └── SKILL.md               # Copied from environment packages
        
        Args:
            runtime_id: Runtime package identifier (e.g., openclaw)
            runtime_content_hash: Optional expected snapshot hash for traceability checks
            initial_content: Optional dict of filename -> content for workspace files
            agent_name: Optional display name used for runtime package placeholder rendering
            environment_url: Optional primary environment URL used for placeholder rendering
            environment_name: Name of the environment for skill bootstrap
            institutional_mode: Whether to use institutional mode skill file
            
        Returns:
            Dict with creation result and bootstrap info
        """
        result = {
            "created": False,
            "agent_path": str(self.agent_path),
            "workspace_path": str(self.workspace_path),
            "skills_path": str(self.skills_path),
            "runtime_id": normalize_agent_runtime_name(runtime_id),
            "runtime_resolution": None,
            "runtime_workspace_loaded": False,
            "runtime_config_copied": False,
            "environment_bootstrap": None,
            "bootstrap_validation": None,
            "render_warnings": [],
            "error": None
        }

        try:
            runtime_resolution = self._resolve_runtime_materialization(
                runtime_id=runtime_id,
                runtime_content_hash=runtime_content_hash,
            )
        except Exception as e:
            result["error"] = str(e)
            return result
        result["runtime_resolution"] = runtime_resolution

        print(f"[DEBUG] Creating agent directory at {self.agent_path}")
        print(f"[DEBUG] Agents base path: {self.agent_path.parent}")
        materialization_started = False

        def _cleanup_partial_creation() -> None:
            if not materialization_started:
                return
            try:
                shutil.rmtree(self.agent_path)
            except FileNotFoundError:
                pass
            except Exception as cleanup_exc:
                print(
                    "[WARNING] Failed to cleanup partial agent directory "
                    f"agent_id={self.agent_id} error={cleanup_exc}"
                )
        
        # Create directories
        print(f"[DEBUG] Creating agent_path: {self.agent_path}")
        self.agent_path.mkdir(parents=True, exist_ok=True)
        materialization_started = True
        print(f"[DEBUG] Creating workspace_path: {self.workspace_path}")
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        print(f"[DEBUG] Creating skills_path: {self.skills_path}")
        self.skills_path.mkdir(parents=True, exist_ok=True)
        print(f"[DEBUG] Directories created successfully")
        
        # Create run metadata file
        run_id_file = self.agent_path / ".run_id"
        async with aiofiles.open(run_id_file, 'w') as f:
            await f.write(self.run_id)

        normalized_runtime_id = str(
            runtime_resolution.get("runtime_id") or normalize_agent_runtime_name(runtime_id)
        )
        runtime_base_path = RUNTIME_PACKAGE_PATH / normalized_runtime_id
        runtime_workspace_path = Path(
            runtime_resolution.get("workspace_source_path") or (runtime_base_path / "workspace")
        )

        # Create standard files with default/runtime/provided content
        default_content = {
            "AGENTS.md": f"# Agent Configuration\n\nAgent ID: {self.agent_id}\nRun: {self.run_id}\n",
            "IDENTITY.md": f"# Agent Identity\n\nAgent ID: {self.agent_id}\nRun: {self.run_id}\n",
            "SOUL.md": "# Agent Soul\n\nPersonality and behavioral traits.\n",
            "TOOLS.md": "# Agent Tools\n\nAvailable tools and capabilities.\n",
            "BOOTSTRAP.md": "# Bootstrap Instructions\n\nInitial setup and bootstrap tasks.\n",
            "USER.md": "# User Configuration\n\nUser-specific settings and preferences.\n",
            "HEARTBEAT.md": "# Agent Heartbeat\n\nLast heartbeat and status information.\n",
        }
        
        if runtime_workspace_path.exists():
            content: Dict[str, str] = {}
            for runtime_file in sorted(runtime_workspace_path.glob("*.md")):
                if not runtime_file.is_file():
                    continue
                content[runtime_file.name] = runtime_file.read_text(encoding="utf-8")
            result["runtime_workspace_loaded"] = True
        else:
            content = dict(default_content)
            print(
                f"[WARNING] Agent runtime workspace not found for '{normalized_runtime_id}' at "
                f"{runtime_workspace_path}; falling back to defaults"
            )

        if initial_content:
            for filename, file_content in initial_content.items():
                if file_content is not None:
                    content[filename] = file_content
        
        normalized_environment_name = normalize_environment_name(environment_name)
        now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        render_values = {
            "AGENT_ID": self.agent_id,
            "AGENT_NAME": str(agent_name or self.agent_id),
            "RUN_ID": self.run_id,
            "ENVIRONMENT_NAME": normalized_environment_name,
            "ENVIRONMENT_ID": normalized_environment_name,
            "ENVIRONMENT_URL": str(environment_url or ""),
            "ENV_URL": str(environment_url or ""),
            "CREATED_AT": now_iso,
            "CREATED_BY": "controller",
            "REG_STATUS": "registered",
        }

        print(f"[DEBUG] Creating {len(content)} files in workspace directory")
        for filename, file_content in content.items():
            rendered_content, unresolved_tokens = _render_placeholders(
                file_content,
                render_values,
                replace_missing=True,
            )
            if unresolved_tokens:
                result["render_warnings"].append(
                    {
                        "file": filename,
                        "unresolved_placeholders": unresolved_tokens,
                        "policy": "optional_markdown_default_empty",
                    }
                )
            filepath = self.workspace_path / filename
            print(f"[DEBUG] Creating file: {filepath}")
            async with aiofiles.open(filepath, 'w') as f:
                await f.write(rendered_content)
        print(f"[DEBUG] All files created successfully")

        # Copy runtime package config.json when available.
        runtime_config_path = Path(
            runtime_resolution.get("config_source_path") or (runtime_base_path / "config.json")
        )
        rendered_agent_config: Dict[str, Any] = {}
        if runtime_config_path.exists():
            target_config_path = self.agent_path / "config.json"
            raw_config = runtime_config_path.read_text(encoding="utf-8")
            rendered_config, unresolved_config_tokens = _render_placeholders(
                raw_config,
                render_values,
                replace_missing=False,
            )
            required_config_tokens = {"AGENT_ID", "AGENT_NAME", "ENVIRONMENT_URL"}
            unresolved_required = sorted(
                token for token in unresolved_config_tokens if token in required_config_tokens
            )
            missing_required_values = sorted(
                token
                for token in required_config_tokens
                if not str(render_values.get(token) or "").strip()
            )
            if unresolved_required:
                result["error"] = (
                    "Unresolved required config placeholders: "
                    + ", ".join(unresolved_required)
                )
                _cleanup_partial_creation()
                return result
            if missing_required_values:
                result["error"] = (
                    "Missing required config placeholder values: "
                    + ", ".join(missing_required_values)
                )
                _cleanup_partial_creation()
                return result
            rendered_config, unresolved_remaining = _render_placeholders(
                rendered_config,
                render_values,
                replace_missing=True,
            )
            if unresolved_remaining:
                result["render_warnings"].append(
                    {
                        "file": "config.json",
                        "unresolved_placeholders": unresolved_remaining,
                        "policy": "optional_config_default_empty",
                    }
                )
            target_config_path.write_text(rendered_config, encoding="utf-8")
            result["runtime_config_copied"] = True
            try:
                parsed_config = json.loads(rendered_config)
                if isinstance(parsed_config, dict):
                    rendered_agent_config = parsed_config
            except Exception:
                rendered_agent_config = {}
        else:
            print(f"[WARNING] Agent runtime config missing at {runtime_config_path}")

        environment_cfg = rendered_agent_config.get("environment")
        install_environment_skill = True
        if isinstance(environment_cfg, dict) and environment_cfg.get("install_environment_skill") is False:
            install_environment_skill = False

        # Bootstrap environment skill if environment name provided
        if environment_name and install_environment_skill:
            print(f"[DEBUG] Bootstrapping environment skill for {environment_name}")
            bootstrap_result = await self.bootstrap_environment_skill(
                environment_name=environment_name,
                environment_url=environment_url,
                institutional_mode=institutional_mode
            )
            result["environment_bootstrap"] = bootstrap_result
            
            if bootstrap_result["success"]:
                await self.seed_workspace_heartbeat_for_environment(environment_name)
                print(f"[DEBUG] Environment skill bootstrapped successfully")
            else:
                print(f"[WARNING] Failed to bootstrap environment skill: {bootstrap_result.get('error')}")
        elif environment_name:
            print(f"[DEBUG] Skipping environment skill bootstrap for {environment_name} due to runtime config")
            result["environment_bootstrap"] = {
                "environment": normalize_environment_name(environment_name),
                "institutional_mode": institutional_mode,
                "success": True,
                "skipped": True,
                "skill_path": None,
                "source_file": None,
                "installed_files": [],
                "error": None,
            }
        
        # Validate BOOTSTRAP.md contains skill reference
        print(f"[DEBUG] Validating BOOTSTRAP.md references")
        validation_result = await self.validate_bootstrap_reference()
        result["bootstrap_validation"] = validation_result
        
        if validation_result["warnings"]:
            print(f"[WARNING] BOOTSTRAP.md validation warnings: {validation_result['warnings']}")
        
        if validation_result["fixes_applied"]:
            print(f"[DEBUG] Applied fixes to BOOTSTRAP.md: {validation_result['fixes_applied']}")
        
        result["created"] = True
        return result
    
    def _resolve_file_path(self, filename: str) -> Path:
        """Resolve user-provided file paths into the agent workspace.

        Agents sometimes emit absolute-like paths (for example
        `/agents/<agent-id>/workspace/HEARTBEAT.md`) or already-prefixed relative
        paths (for example `workspace/HEARTBEAT.md`). Skill reads can target
        `skills/<skill>/...` paths. Normalize these to workspace/skills roots.
        """
        clean_filename = str(filename or "").replace("\\", "/").strip()
        if not clean_filename:
            return self.workspace_path

        parts = [part for part in clean_filename.split("/") if part and part != "."]

        # Normalize skill-root paths first.
        while parts:
            if len(parts) >= 3 and parts[0] == "agents" and parts[1] == self.agent_id and parts[2] == "skills":
                parts = parts[3:]
                return self.skills_path / Path(*parts) if parts else self.skills_path
            if len(parts) >= 2 and parts[0] == self.agent_id and parts[1] == "skills":
                parts = parts[2:]
                return self.skills_path / Path(*parts) if parts else self.skills_path
            if parts[0] == "skills":
                parts = parts[1:]
                return self.skills_path / Path(*parts) if parts else self.skills_path
            break

        # Strip repeated known prefixes to avoid `workspace/workspace/...` drift.
        while parts:
            if len(parts) >= 3 and parts[0] == "agents" and parts[1] == self.agent_id and parts[2] == "workspace":
                parts = parts[3:]
                continue
            if len(parts) >= 2 and parts[0] == self.agent_id and parts[1] == "workspace":
                parts = parts[2:]
                continue
            if parts[0] == "workspace":
                parts = parts[1:]
                continue
            break

        if not parts:
            return self.workspace_path
        return self.workspace_path / Path(*parts)
    
    async def read_file(self, filename: str) -> Optional[str]:
        """Read a file from the agent workspace directory."""
        filepath = self._resolve_file_path(filename)
        if not filepath.exists():
            return None
        
        async with aiofiles.open(filepath, 'r') as f:
            return await f.read()
    
    async def write_file(self, filename: str, content: str) -> None:
        """Write content to a file in the agent workspace directory."""
        filepath = self._resolve_file_path(filename)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(filepath, 'w') as f:
            await f.write(content)
    
    async def append_file(self, filename: str, content: str) -> None:
        """Append content to a file in the agent workspace directory."""
        filepath = self._resolve_file_path(filename)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(filepath, 'a') as f:
            await f.write(content)
    
    async def delete_file(self, filename: str) -> bool:
        """Delete a file from the agent workspace directory."""
        filepath = self._resolve_file_path(filename)
        if filepath.exists():
            filepath.unlink()
            return True
        return False
    
    async def file_exists(self, filename: str) -> bool:
        """Check if a file exists in the agent workspace directory."""
        filepath = self._resolve_file_path(filename)
        return filepath.exists()

    async def load_agent_config(self) -> Dict[str, Any]:
        """Load agent-level config.json (best effort, non-throwing)."""
        config_path = self.agent_path / "config.json"
        if not config_path.exists() or not config_path.is_file():
            return {}
        try:
            async with aiofiles.open(config_path, "r") as f:
                raw = await f.read()
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    
    async def assemble_system_prompt(
        self,
        skill_content: Optional[str] = None,
        manifest_content: Optional[str] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Assemble system prompt from all agent files.
        
        Order (per OpenClaw-base contract):
        1. AGENTS.md
        2. IDENTITY.md
        3. SOUL.md
        4. TOOLS.md
        5. BOOTSTRAP.md
        6. USER.md
        7. HEARTBEAT.md
        8. Available skills metadata list (name/description/location)
        9. skill.md from environment (if provided - legacy)
        10. manifest (reserved for future environment-local extensions)
        11. runtime context (if provided)
        """
        system_prompt, _parts = await self.assemble_system_prompt_with_parts(
            skill_content=skill_content,
            manifest_content=manifest_content,
            runtime_context=runtime_context,
        )
        return system_prompt

    async def assemble_system_prompt_with_parts(
        self,
        skill_content: Optional[str] = None,
        manifest_content: Optional[str] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
        *,
        dynamic_tail_chars: int = 4000,
        heartbeat_tail_chars: Optional[int] = None,
        max_skill_chars: Optional[int] = None,
        max_prompt_chars: Optional[int] = None,
        single_skill_mode: bool = False,
        preferred_skill_name: Optional[str] = None,
        tool_summaries: Optional[Dict[str, str]] = None,
        treat_heartbeat_as_dynamic: bool = True,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Assemble system prompt and return per-part hashes for telemetry/UI.

        Notes:
        - This preserves the exact concatenation semantics of `assemble_system_prompt`.
        - Dynamic files (HEARTBEAT.md + runtime context) can be large; we only return a tail-preview in `content`.
        - Hashes are computed on the full chunk string used in the system prompt.
        """

        agent_config = await self.load_agent_config()
        raw_skills_cfg = (
            agent_config.get("skills")
            if isinstance(agent_config.get("skills"), dict)
            else {}
        )
        skill_allowlist = (
            [
                normalize_environment_name(item)
                for item in (raw_skills_cfg.get("allowlist") or [])
                if str(item or "").strip()
            ]
            if isinstance(raw_skills_cfg.get("allowlist"), list)
            else None
        )
        try:
            cfg_max_skills_items = (
                int(raw_skills_cfg.get("max_skills_in_prompt"))
                if raw_skills_cfg.get("max_skills_in_prompt") is not None
                else SKILLS_PROMPT_MAX_ITEMS_DEFAULT
            )
        except Exception:
            cfg_max_skills_items = SKILLS_PROMPT_MAX_ITEMS_DEFAULT
        if cfg_max_skills_items <= 0:
            cfg_max_skills_items = SKILLS_PROMPT_MAX_ITEMS_DEFAULT
        try:
            cfg_max_skills_chars = (
                int(raw_skills_cfg.get("max_skills_prompt_chars"))
                if raw_skills_cfg.get("max_skills_prompt_chars") is not None
                else SKILLS_PROMPT_MAX_CHARS_DEFAULT
            )
        except Exception:
            cfg_max_skills_chars = SKILLS_PROMPT_MAX_CHARS_DEFAULT
        if cfg_max_skills_chars <= 0:
            cfg_max_skills_chars = SKILLS_PROMPT_MAX_CHARS_DEFAULT
        if max_skill_chars and max_skill_chars > 0:
            cfg_max_skills_chars = min(cfg_max_skills_chars, max_skill_chars)

        context_files: List[ContextFile] = []

        # Read standard files in order
        for filename in STANDARD_FILES:
            content = await self.read_file(filename)
            if not content:
                continue
            truncated = False
            truncation_reason: Optional[str] = None
            if filename == "HEARTBEAT.md":
                content, _entries = _split_heartbeat_log(content)
                content, truncated = _truncate_text(
                    content,
                    max_chars=heartbeat_tail_chars,
                    prefer_tail=True,
                    marker_label="HEARTBEAT.md",
                )
                if truncated:
                    truncation_reason = "heartbeat_tail_budget"
            context_files.append(
                ContextFile(
                    path=filename,
                    content=content,
                    dynamic=(filename == "HEARTBEAT.md" and treat_heartbeat_as_dynamic),
                    truncated=truncated,
                    truncation_reason=truncation_reason,
                )
            )

        # Add available skills metadata (OpenClaw-standard prompt contract).
        # NOTE: single_skill_mode is intentionally retained only for config compatibility.
        available_skills_content, available_skills_stats = await self.build_available_skills_prompt(
            skill_allowlist=skill_allowlist,
            max_skills_in_prompt=cfg_max_skills_items,
            max_skills_prompt_chars=cfg_max_skills_chars,
        )
        # Add skill content from environment if provided (legacy support)
        if skill_content:
            context_files.append(
                ContextFile(
                    path="skill.md",
                    content=skill_content,
                    dynamic=False,
                    extra={"kind_override": "environment_skill_legacy"},
                )
            )

        # Reserved for future environment-local extensions.
        if manifest_content:
            context_files.append(
                ContextFile(
                    path="manifest",
                    content=manifest_content,
                    dynamic=False,
                    extra={"kind_override": "runtime_manifest"},
                )
            )

        runtime_info = {
            "agent_id": self.agent_id,
            "channel": (runtime_context or {}).get("interaction_mode"),
            "thinking": "off",
        }
        for source_key, target_key in (
            ("run_id", "repo_root"),
            ("model", "model"),
            ("environment_name", "host"),
        ):
            value = (runtime_context or {}).get(source_key)
            if value is not None:
                runtime_info[target_key] = value

        prompt, meta = build_openclaw_system_prompt_parts(
            workspace_dir=str(self.workspace_path),
            heartbeat_prompt=(runtime_context or {}).get("heartbeat_prompt") or "",
            skills_prompt=available_skills_content,
            context_files=context_files,
            runtime_info=runtime_info,
            tool_summaries=tool_summaries,
            dynamic_tail_chars=dynamic_tail_chars,
        )

        for entry in meta:
            if entry.get("name") == "Skills":
                entry["kind"] = "available_skills"
                entry["truncated"] = bool(available_skills_stats.get("truncated"))
                entry["truncation_reason"] = available_skills_stats.get("truncation_reason")
                entry["skills_count"] = int(available_skills_stats.get("skills_count") or 0)
                entry["skills_total"] = int(available_skills_stats.get("skills_total") or 0)
            elif entry.get("kind") == "workspace_file":
                if entry.get("name") == "skill.md":
                    entry["kind"] = "environment_skill_legacy"
                elif entry.get("name") == "manifest":
                    entry["kind"] = "runtime_manifest"

        if max_prompt_chars and max_prompt_chars > 0 and len(prompt) > max_prompt_chars:
            prompt, _truncated = _truncate_text(
                prompt,
                max_chars=max_prompt_chars,
                prefer_tail=False,
                marker_label="system_prompt",
            )
            marker_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            meta.append(
                {
                    "name": "PROMPT_BUDGET",
                    "kind": "prompt_budget",
                    "dynamic": False,
                    "sha256": marker_sha,
                    "bytes": len(prompt.encode("utf-8")),
                    "content": "System prompt truncated by max_prompt_chars budget.",
                    "truncated": True,
                    "truncation_reason": "max_prompt_chars",
                }
            )

        return prompt, meta
    
    async def update_heartbeat(self, status: str, result_summary: Optional[str] = None) -> None:
        """Append heartbeat runtime status outside prompt-visible HEARTBEAT.md."""
        timestamp = datetime.now().isoformat()

        heartbeat_entry = f"""## Heartbeat Update

- **Timestamp**: {timestamp}
- **Status**: {status}
        """
        if result_summary:
            heartbeat_entry += f"- **Summary**: {result_summary}\n"

        existing = await self.read_file("HEARTBEAT.md") or ""
        prefix, _entries = _split_heartbeat_log(existing)
        if prefix != existing.rstrip():
            await self.write_file("HEARTBEAT.md", f"{prefix}\n" if prefix else "")

        runtime_log_path = self._heartbeat_runtime_log_path()
        if runtime_log_path.exists():
            async with aiofiles.open(runtime_log_path, "r") as f:
                runtime_existing = await f.read()
        else:
            runtime_existing = ""

        _runtime_prefix, entries = _split_heartbeat_log(runtime_existing)
        entries.append(heartbeat_entry.strip())
        if len(entries) > HEARTBEAT_LOG_MAX_ENTRIES:
            entries = entries[-HEARTBEAT_LOG_MAX_ENTRIES:]

        rendered = _compose_heartbeat_log("", entries)
        while len(rendered) > HEARTBEAT_LOG_MAX_CHARS and len(entries) > 1:
            entries = entries[1:]
            rendered = _compose_heartbeat_log("", entries)

        if len(rendered) > HEARTBEAT_LOG_MAX_CHARS:
            rendered, _ = _truncate_text(
                rendered,
                max_chars=HEARTBEAT_LOG_MAX_CHARS,
                prefer_tail=True,
                marker_label="HEARTBEAT.runtime",
            )

        async with aiofiles.open(runtime_log_path, "w") as f:
            await f.write(rendered)

    async def get_recent_heartbeat_entries(self, limit: int) -> List[str]:
        """Return latest runtime heartbeat-log entries (oldest->newest within window)."""
        if limit <= 0:
            return []
        runtime_log_path = self._heartbeat_runtime_log_path()
        if not runtime_log_path.exists():
            return []
        async with aiofiles.open(runtime_log_path, "r") as f:
            existing = await f.read()
        _prefix, entries = _split_heartbeat_log(existing)
        if not entries:
            return []
        return entries[-limit:]

    async def append_runtime_heartbeat_entry(self, entry: str) -> None:
        """Append a raw runtime entry to the hidden heartbeat log file."""
        text = str(entry or "").strip()
        if not text:
            return

        runtime_log_path = self._heartbeat_runtime_log_path()
        if runtime_log_path.exists():
            async with aiofiles.open(runtime_log_path, "r") as f:
                existing = await f.read()
        else:
            existing = ""

        _prefix, entries = _split_heartbeat_log(existing)
        entries.append(text)
        if len(entries) > HEARTBEAT_LOG_MAX_ENTRIES:
            entries = entries[-HEARTBEAT_LOG_MAX_ENTRIES:]

        rendered = _compose_heartbeat_log("", entries)
        while len(rendered) > HEARTBEAT_LOG_MAX_CHARS and len(entries) > 1:
            entries = entries[1:]
            rendered = _compose_heartbeat_log("", entries)

        async with aiofiles.open(runtime_log_path, "w") as f:
            await f.write(rendered)

    async def get_recent_session_messages(self, limit: int, *, max_chars: int) -> List[Dict[str, str]]:
        """Load recent persisted session transcript messages for heartbeat continuity."""
        if limit <= 0:
            return []
        session_path = self._heartbeat_session_path()
        if not session_path.exists():
            return []
        try:
            payload = json.loads(session_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []

        normalized: List[Dict[str, str]] = []
        total_chars = 0
        for item in reversed(payload):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            projected = total_chars + len(content)
            if normalized and projected > max(max_chars, 1):
                break
            normalized.append({"role": role, "content": content})
            total_chars = projected
            if len(normalized) >= limit:
                break

        normalized.reverse()
        return normalized

    async def store_session_messages(
        self,
        messages: List[Dict[str, str]],
        *,
        limit: int,
        max_chars: int,
    ) -> None:
        """Persist bounded session transcript for future heartbeat continuity."""
        sanitized: List[Dict[str, str]] = []
        total_chars = 0
        for item in reversed(list(messages or [])):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            projected = total_chars + len(content)
            if sanitized and projected > max(max_chars, 1):
                break
            sanitized.append({"role": role, "content": content})
            total_chars = projected
            if len(sanitized) >= limit:
                break
        sanitized.reverse()
        self._heartbeat_session_path().write_text(
            json.dumps(sanitized, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    
    async def complete_bootstrap(self) -> bool:
        """Mark bootstrap as complete by deleting BOOTSTRAP.md.
        
        Per OpenClaw-base contract: BOOTSTRAP.md is deleted only after 
        successful first run (bootstrap completion).
        
        Returns:
            True if bootstrap file was deleted, False if it didn't exist
        """
        return await self.delete_file("BOOTSTRAP.md")
    
    async def is_bootstrap_complete(self) -> bool:
        """Check if bootstrap has been completed (BOOTSTRAP.md no longer exists)."""
        return not await self.file_exists("BOOTSTRAP.md")
    
    async def get_agent_info(self) -> Dict[str, Any]:
        """Get information about the agent."""
        info = {
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "path": str(self.agent_path),
            "workspace_path": str(self.workspace_path),
            "skills_path": str(self.skills_path),
            "exists": await self.exists(),
            "files": {},
            "bootstrap_complete": await self.is_bootstrap_complete(),
        }
        
        if info["exists"]:
            # Get sizes of all standard files
            for filename in STANDARD_FILES:
                filepath = self.workspace_path / filename
                if filepath.exists():
                    info["files"][filename] = {
                        "size": filepath.stat().st_size,
                        "modified": datetime.fromtimestamp(
                            filepath.stat().st_mtime
                        ).isoformat(),
                    }
        
        return info
    
    async def delete_agent(self) -> bool:
        """Delete the agent directory and all contents."""
        if not await self.exists():
            return False
        
        import shutil
        shutil.rmtree(self.agent_path)
        return True
    
    async def install_skill(
        self,
        skill_name: str,
        environment_url: str,
        skill_files: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Install a skill from environment to local agent filesystem.
        
        Per Skill Acquisition Contract (§3.2):
        1. Agent reads environment skill metadata (GET /skill.md)
        2. Agent registers with environment (POST /auth/register)
        3. Agent installs desired skill files under /agents/{id}/skills/{name}/
        
        Copies SKILL.md plus any linked companion docs (for example HEARTBEAT.md, MESSAGING.md, RULES.md).
        Stores in /agents/{agent_id}/skills/{skill_name}/
        
        Args:
            skill_name: Name of the skill to install
            environment_url: Base URL of the environment service
            skill_files: Optional pre-fetched skill files (SKILL.md content, etc.)
        
        Returns:
            Dict with installation result and list of installed files
        """
        skill_path = self.skills_path / skill_name
        skill_path.mkdir(parents=True, exist_ok=True)
        
        installed_files = []
        errors = []
        
        # Files to fetch/install per contract
        requested_files: List[str] = ["SKILL.md"]
        if skill_files:
            for filename in skill_files.keys():
                normalized = str(filename or "").strip()
                if normalized and normalized not in requested_files:
                    requested_files.append(normalized)

        for filename in requested_files:
            content = None
            
            # If skill_files provided, use that content
            if skill_files and filename in skill_files:
                content = skill_files[filename]
            else:
                # Fetch from environment
                content = await self._fetch_skill_file(environment_url, skill_name, filename)
            
            if content:
                filepath = skill_path / filename
                async with aiofiles.open(filepath, 'w') as f:
                    await f.write(content)
                installed_files.append(filename)
            elif filename == "SKILL.md":
                # SKILL.md is required
                errors.append(f"Required file {filename} not found")
            # Linked companion docs are optional

        skill_content = skill_files.get("SKILL.md") if skill_files else None
        if not skill_content and "SKILL.md" in installed_files:
            filepath = skill_path / "SKILL.md"
            async with aiofiles.open(filepath, "r") as f:
                skill_content = await f.read()

        for filename in _linked_skill_doc_filenames(skill_content):
            if filename in installed_files:
                continue
            content = None
            if skill_files and filename in skill_files:
                content = skill_files[filename]
            else:
                content = await self._fetch_skill_file(environment_url, skill_name, filename)
            if content:
                filepath = skill_path / filename
                async with aiofiles.open(filepath, "w") as f:
                    await f.write(content)
                installed_files.append(filename)

        return {
            "skill_name": skill_name,
            "skill_path": str(skill_path),
            "installed_files": installed_files,
            "errors": errors,
            "success": len(errors) == 0 and "SKILL.md" in installed_files
        }
    
    async def _fetch_skill_file(
        self,
        environment_url: str,
        skill_name: str,
        filename: str
    ) -> Optional[str]:
        """Fetch a skill file from the environment.
        
        Args:
            environment_url: Base URL of the environment service
            skill_name: Name of the skill
            filename: Name of the file to fetch (e.g., SKILL.md)
        
        Returns:
            File content or None if not found
        """
        if not environment_url:
            return None
        
        # Try skill-specific endpoint first
        urls_to_try = [
            f"{environment_url.rstrip('/')}/skills/{skill_name}/{filename}",
            f"{environment_url.rstrip('/')}/{filename.lower()}",  # Legacy: /skill.md
        ]
        
        for url in urls_to_try:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url)
                    if response.status_code == 200:
                        return response.text
            except Exception:
                continue
        
        return None
    
    async def get_installed_skills(self) -> List[str]:
        """Get list of installed skill names.
        
        Returns:
            List of skill names (directory names in skills/ folder)
        """
        if not self.skills_path.exists():
            return []
        
        skills = []
        for item in self.skills_path.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                skills.append(item.name)
        
        return skills

    async def build_available_skills_prompt(
        self,
        *,
        skill_allowlist: Optional[List[str]] = None,
        max_skills_in_prompt: Optional[int] = None,
        max_skills_prompt_chars: Optional[int] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Build OpenClaw-style available skills metadata block.

        Returns:
            Tuple of (metadata_block, stats)
        """
        stats = {
            "skills_total": 0,
            "skills_count": 0,
            "truncated": False,
            "truncation_reason": None,
        }
        installed_skills = sorted(await self.get_installed_skills())
        if not installed_skills:
            return "", stats

        allowset = {
            normalize_environment_name(name)
            for name in (skill_allowlist or [])
            if str(name or "").strip()
        }
        if allowset:
            installed_skills = [name for name in installed_skills if name in allowset]
        stats["skills_total"] = len(installed_skills)
        if not installed_skills:
            return "", stats

        max_items = max_skills_in_prompt or SKILLS_PROMPT_MAX_ITEMS_DEFAULT
        if max_items < len(installed_skills):
            installed_skills = installed_skills[:max_items]
            stats["truncated"] = True
            stats["truncation_reason"] = "skills_count_budget"

        skill_lines: List[str] = ["<available_skills>"]
        max_chars = max_skills_prompt_chars or SKILLS_PROMPT_MAX_CHARS_DEFAULT

        for skill_name in installed_skills:
            skill_md = await self.read_skill_file(skill_name, "SKILL.md")
            description = _skill_prompt_description(skill_md or "", skill_name)
            candidate_lines = [
                "  <skill>",
                f"    <name>{xml_escape(skill_name)}</name>",
                f"    <description>{xml_escape(description[:300])}</description>",
                f"    <location>{xml_escape(f'skills/{skill_name}/SKILL.md')}</location>",
                "  </skill>",
            ]
            if max_chars and max_chars > 0:
                candidate = "\n".join(skill_lines + candidate_lines + ["</available_skills>"])
                if len(candidate) > max_chars:
                    stats["truncated"] = True
                    if not stats["truncation_reason"]:
                        stats["truncation_reason"] = "skills_char_budget"
                    break
            skill_lines.extend(candidate_lines)
            stats["skills_count"] += 1

        skill_lines.append("</available_skills>")
        if stats["skills_count"] == 0:
            return "", stats
        return "\n".join(skill_lines), stats
    
    async def read_skill_file(self, skill_name: str, filename: str) -> Optional[str]:
        """Read a file from an installed skill.
        
        Args:
            skill_name: Name of the skill
            filename: Name of the file to read
        
        Returns:
            File content or None if not found
        """
        filepath = self.skills_path / skill_name / filename
        if not filepath.exists():
            return None
        
        async with aiofiles.open(filepath, 'r') as f:
            return await f.read()
    
    async def load_all_skills_content(
        self,
        *,
        single_skill_mode: bool = False,
        preferred_skill_name: Optional[str] = None,
    ) -> str:
        """Load content from all installed skills for prompt assembly.
        
        Per contract: Format is skills/{skill}/SKILL.md + skills/{skill}/HEARTBEAT.md
        
        Returns:
            Combined skill content for LLM prompt
        """
        skills = await self.get_installed_skills()
        if not skills:
            return ""

        selected_skills = sorted(skills)
        if single_skill_mode:
            normalized_preferred = normalize_environment_name(preferred_skill_name)
            if normalized_preferred and normalized_preferred in selected_skills:
                selected_skills = [normalized_preferred]
            else:
                selected_skills = [selected_skills[0]]
        
        parts = []
        
        for skill_name in selected_skills:
            skill_parts = []
            
            # Load SKILL.md
            skill_md = await self.read_skill_file(skill_name, "SKILL.md")
            if skill_md:
                skill_parts.append(f"--- {skill_name}/SKILL.md ---\n{skill_md}")
            
            # Load HEARTBEAT.md if exists
            heartbeat_md = await self.read_skill_file(skill_name, "HEARTBEAT.md")
            if heartbeat_md:
                skill_parts.append(f"--- {skill_name}/HEARTBEAT.md ---\n{heartbeat_md}")
            
            # Load MESSAGING.md if exists
            messaging_md = await self.read_skill_file(skill_name, "MESSAGING.md")
            if messaging_md:
                skill_parts.append(f"--- {skill_name}/MESSAGING.md ---\n{messaging_md}")
            
            if skill_parts:
                parts.append(f"\n## Skill: {skill_name}\n\n" + "\n\n".join(skill_parts))
        
        if parts:
            header = "\n\n# Installed Skills\n\n"
            if single_skill_mode:
                header += (
                    f"single_skill_mode: true\n"
                    f"selected_skill: {selected_skills[0]}\n\n"
                )
            return header + "\n\n".join(parts)
        
        return ""
