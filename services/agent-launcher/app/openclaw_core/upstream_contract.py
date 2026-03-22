"""Source-backed OpenClaw core invariants frozen for the Python replica."""

from __future__ import annotations

from typing import Dict, Tuple


UPSTREAM_SOURCES: Dict[str, str] = {
    "system_prompt_source": "https://github.com/openclaw/openclaw/blob/main/src/agents/system-prompt.ts",
    "system_prompt_docs": "https://docs.openclaw.ai/concepts/system-prompt",
    "agent_loop_docs": "https://docs.openclaw.ai/agent-loop",
    "heartbeat_docs": "https://docs.openclaw.ai/gateway/heartbeat",
}

SYSTEM_PROMPT_CORE_SECTION_ORDER: Tuple[str, ...] = (
    "OpenClaw",
    "Tooling",
    "Tool Call Style",
    "Safety",
    "Skills",
    "Workspace",
    "Workspace Files (injected)",
    "Heartbeats",
    "Runtime",
)

HEARTBEAT_ACK_TEXT = "HEARTBEAT_OK"

HEARTBEAT_CORE_RULES: Tuple[str, ...] = (
    "If you receive a heartbeat poll and there is nothing that needs attention, reply exactly HEARTBEAT_OK.",
    'OpenClaw may treat a leading or trailing "HEARTBEAT_OK" as a heartbeat acknowledgement.',
    'If something needs attention, do not include "HEARTBEAT_OK"; reply with the alert text instead.',
)

CORE_RUNTIME_BEHAVIORS: Tuple[str, ...] = (
    "runtime_built_system_prompt",
    "workspace_markdown_injection",
    "available_skills_exposure",
    "heartbeat_ack_semantics",
    "tooling_section_emitted_at_runtime",
    "runtime_line_emitted_at_runtime",
)

EXCLUDED_PRODUCT_SHELL_BEHAVIORS: Tuple[str, ...] = (
    "gateway_cli_commands",
    "self_update_workflows",
    "reactions",
    "voice_tts_guidance",
    "channel_specific_messaging_shell",
    "full_product_tool_inventory",
)

LEGACY_MASE_OWNERSHIP_MAP: Dict[str, str] = {
    "services/agent-launcher/app/agent_fs.py": "legacy_runtime_prompt_assembly",
    "services/agent-launcher/app/heartbeat_runtime.py": "legacy_inner_loop_and_observation_shaping",
    "services/agent-launcher/app/action_parser.py": "legacy_model_output_contract_adapter",
    "services/agent-launcher/app/routes/launcher.py": "legacy_runtime_to_telemetry_bridge",
    "runtimes/openclaw/config.json": "legacy_parity_runtime_policy",
}
