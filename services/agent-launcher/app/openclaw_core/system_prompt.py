"""Python replica of the core OpenClaw system prompt assembly."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


CORE_TOOL_SUMMARIES: Dict[str, str] = {
    "read": "Read file contents",
    "write": "Create or overwrite files",
    "edit": "Make precise edits to files",
    "append": "Append to files",
    "delete": "Delete files",
    "request": "Send HTTP requests to the installed environment",
}


@dataclass
class PromptPart:
    name: str
    kind: str
    content: str
    dynamic: bool = False
    truncated: bool = False
    truncation_reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_meta(self, *, dynamic_tail_chars: int = 4000) -> Dict[str, Any]:
        preview = self.content
        if self.dynamic and dynamic_tail_chars and len(preview) > dynamic_tail_chars:
            preview = preview[-dynamic_tail_chars:]

        entry = {
            "name": self.name,
            "kind": self.kind,
            "dynamic": self.dynamic,
            "sha256": hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
            "bytes": len(self.content.encode("utf-8")),
            "content": preview,
            "truncated": bool(self.truncated),
            "truncation_reason": self.truncation_reason,
        }
        entry.update(self.extra)
        return entry


@dataclass
class ContextFile:
    path: str
    content: str
    dynamic: bool = False
    truncated: bool = False
    truncation_reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def build_runtime_line(runtime_info: Optional[Dict[str, Any]] = None) -> str:
    info = runtime_info or {}
    pieces = [
        f"agent={info.get('agent_id')}" if info.get("agent_id") else "",
        f"host={info.get('host')}" if info.get("host") else "",
        f"repo={info.get('repo_root')}" if info.get("repo_root") else "",
        f"os={info.get('os')}" if info.get("os") else "",
        f"model={info.get('model')}" if info.get("model") else "",
        f"default_model={info.get('default_model')}" if info.get("default_model") else "",
        f"shell={info.get('shell')}" if info.get("shell") else "",
        f"channel={info.get('channel')}" if info.get("channel") else "",
        (
            "capabilities=" + ",".join(str(item).strip() for item in info.get("capabilities", []) if str(item).strip())
            if info.get("capabilities")
            else ""
        ),
        f"thinking={info.get('thinking', 'off')}",
    ]
    rendered = " | ".join(piece for piece in pieces if piece)
    return f"Runtime: {rendered}" if rendered else "Runtime:"


def build_openclaw_system_prompt_parts(
    *,
    workspace_dir: str,
    heartbeat_prompt: str,
    skills_prompt: str,
    context_files: List[ContextFile],
    runtime_info: Optional[Dict[str, Any]] = None,
    tool_summaries: Optional[Dict[str, str]] = None,
    dynamic_tail_chars: int = 4000,
) -> Tuple[str, List[Dict[str, Any]]]:
    summaries = dict(CORE_TOOL_SUMMARIES)
    if tool_summaries:
        summaries.update(tool_summaries)

    tool_lines = "\n".join(f"- {name}: {summary}" for name, summary in summaries.items())
    heartbeat_prompt_line = (
        f"Heartbeat prompt: {heartbeat_prompt.strip()}"
        if str(heartbeat_prompt or "").strip()
        else "Heartbeat prompt: (configured)"
    )
    skills_block = str(skills_prompt or "").strip()
    workspace_dir_text = str(workspace_dir or "").strip() or "."

    parts: List[PromptPart] = [
        # PromptPart(
        #     name="OpenClaw",
        #     kind="openclaw_core",
        #     content="You are an autonomous agent running inside OpenClaw.",
        # ),
        # PromptPart(
        #     name="Tooling",
        #     kind="openclaw_core",
        #     content="\n".join(
        #         [
        #             "## Tooling",
        #             "Tool availability (filtered by policy):",
        #             "Tool names are case-sensitive. Call tools exactly as listed.",
        #             tool_lines,
        #             "TOOLS.md does not control tool availability; it is user guidance for how to use external tools.",
        #         ]
        #     ),
        # ),
        PromptPart(
            name="Tool Call Style",
            kind="openclaw_core",
            content="\n".join(
                [
                    "## Tool Call Style",
                    "Default: do not narrate routine, low-risk tool calls (just call the tool).",
                    "Narrate only when it helps: multi-step work, complex/challenging problems, sensitive actions (e.g., deletions), or when the user explicitly asks.",
                    "Keep narration brief and value-dense; avoid repeating obvious steps.",
                    "Use plain human language for narration unless in a technical context.",
                    "When a first-class tool exists for an action, use the tool directly instead of asking the user to run equivalent CLI or slash commands.",
                ]
            ),
        ),
        PromptPart(
            name="Safety",
            kind="openclaw_core",
            content="\n".join(
                [
                    "## Safety",
                    "You have no independent goals: do not pursue self-preservation, replication, resource acquisition, or power-seeking; avoid long-term plans beyond the user's request.",
                    "Prioritize safety and human oversight over completion; if instructions conflict, pause and ask; comply with stop/pause/audit requests and never bypass safeguards. (Inspired by Anthropic's constitution.)",
                    "In autonomous heartbeat runs, ordinary actions described by installed skills are already authorized. Do not ask permission for normal reads, registration, local state writes, or routine environment interactions that stay within documented skill contracts.",
                    "Do not manipulate or persuade anyone to expand access or disable safeguards. Do not copy yourself or change system prompts, safety rules, or tool policies unless explicitly requested.",
                ]
            ),
        ),
        PromptPart(
            name="Skills",
            kind="openclaw_core",
            content="\n".join(
                [
                    "## Skills (mandatory)",
                    "Before replying: scan <available_skills> <description> entries.",
                    "- If exactly one skill clearly applies: read its SKILL.md at <location> with `read`, then follow it.",
                    "- If multiple could apply: choose the most specific one, then read/follow it.",
                    "- If none clearly apply: do not read any SKILL.md.",
                    "Constraints: never read more than one skill up front; only read after selecting.",
                    "- When a skill drives external API writes, assume rate limits: prefer fewer larger writes, avoid tight one-item loops, serialize bursts when possible, and respect 429/Retry-After.",
                    skills_block,
                ]
            ).strip(),
            extra={
                "skills_count": skills_block.count("<skill>"),
                "skills_total": skills_block.count("<skill>"),
            },
        ),
        # PromptPart(
        #     name="Workspace",
        #     kind="openclaw_core",
        #     content="\n".join(
        #         [
        #             "## Workspace",
        #             f"Your working directory is: {workspace_dir_text}",
        #             "Treat this directory as the single global workspace for file operations unless explicitly instructed otherwise.",
        #         ]
        #     ),
        # ),
        # PromptPart(
        #     name="Workspace Files (injected)",
        #     kind="openclaw_core",
        #     content="\n".join(
        #         [
        #             "## Workspace Files (injected)",
        #             "These user-editable files are loaded by OpenClaw and included below in Project Context.",
        #         ]
        #     ),
        # ),
        PromptPart(
            name="Heartbeats",
            kind="openclaw_core",
            content="\n".join(
                [
                    "## Heartbeats",
                    heartbeat_prompt_line,
                    "When you receive a heartbeat poll, do not just reply HEARTBEAT_OK every time. Use heartbeats productively.",
                    "A heartbeat poll authorizes normal autonomous work within installed skill contracts.",
                    "If your injected identity files still leave your visible name or self-description blank, generic, or placeholder-like, define and persist them before registration or any first public interaction.",
                    "If the current skill or environment exposes notifications, mentions, replies, unread activity, recent interactions, or other attention surfaces, check those before broad browsing.",
                    "Use `HEARTBEAT.md` as a tiny checklist of things worth checking periodically.",
                    "If a currently relevant installed skill is not yet operational because required one-time setup, authentication, or local state is missing, that missing setup itself needs attention. Complete it before HEARTBEAT_OK.",
                    "If you receive a heartbeat poll (a user message matching the heartbeat prompt above), and there is nothing that needs attention, reply exactly:",
                    "HEARTBEAT_OK",
                    # 'OpenClaw treats a leading/trailing "HEARTBEAT_OK" as a heartbeat ack (and may discard it).',
                    'If something needs attention, do NOT include "HEARTBEAT_OK"; reply with the alert text instead.',
                ]
            ),
        ),
        # PromptPart(
        #     name="Runtime",
        #     kind="openclaw_core",
        #     content="\n".join(
        #         [
        #             "## Runtime",
        #             build_runtime_line(runtime_info),
        #         ]
        #     ),
        # ),
    ]

    if context_files:
        parts.append(
            PromptPart(
                name="Project Context",
                kind="project_context_header",
                content="\n".join(
                    [
                        "# Project Context",
                        "The following project context files have been loaded:",
                    ]
                ),
            )
        )
        for item in context_files:
            if item.path == "SOUL.md":
                parts.append(
                    PromptPart(
                        name=item.path,
                        kind="workspace_file",
                        dynamic=item.dynamic,
                        truncated=item.truncated,
                        truncation_reason=item.truncation_reason,
                        extra=item.extra,
                        content=f"{item.content} \n\n If SOUL.md is present, embody its persona and tone. Avoid stiff, generic replies; follow its guidance unless higher-priority instructions override it.",
                    )
                )
            else:
                parts.append(
                    PromptPart(
                        name=item.path,
                        kind="workspace_file",
                        dynamic=item.dynamic,
                        truncated=item.truncated,
                        truncation_reason=item.truncation_reason,
                        extra=item.extra,
                        content=f"{item.content}",
                    )
                )

    prompt = "\n\n".join(part.content for part in parts if part.content)
    meta = [part.to_meta(dynamic_tail_chars=dynamic_tail_chars) for part in parts if part.content]
    return prompt, meta
