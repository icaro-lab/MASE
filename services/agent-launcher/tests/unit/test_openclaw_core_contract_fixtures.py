"""Unit tests for frozen OpenClaw core contract fixtures."""

import json
from pathlib import Path

import pytest

from app.openclaw_core.upstream_contract import (
    CORE_RUNTIME_BEHAVIORS,
    EXCLUDED_PRODUCT_SHELL_BEHAVIORS,
    HEARTBEAT_ACK_TEXT,
    HEARTBEAT_CORE_RULES,
    LEGACY_MASE_OWNERSHIP_MAP,
    SYSTEM_PROMPT_CORE_SECTION_ORDER,
    UPSTREAM_SOURCES,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "openclaw_core"


def _load_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.unit
def test_system_prompt_fixture_matches_frozen_section_order() -> None:
    payload = _load_json("system_prompt_sections.json")

    assert payload["source"] == UPSTREAM_SOURCES["system_prompt_source"]
    assert tuple(payload["core_section_order"]) == SYSTEM_PROMPT_CORE_SECTION_ORDER
    assert payload["runtime_built"] is True


@pytest.mark.unit
def test_heartbeat_fixture_matches_frozen_ack_contract() -> None:
    payload = _load_json("heartbeat_contract.json")

    assert payload["source"] == UPSTREAM_SOURCES["heartbeat_docs"]
    assert payload["ack_text"] == HEARTBEAT_ACK_TEXT
    assert tuple(payload["rules"]) == HEARTBEAT_CORE_RULES


@pytest.mark.unit
def test_core_contract_lists_runtime_behaviors_and_excluded_product_shell() -> None:
    assert "runtime_built_system_prompt" in CORE_RUNTIME_BEHAVIORS
    assert "heartbeat_ack_semantics" in CORE_RUNTIME_BEHAVIORS
    assert "gateway_cli_commands" in EXCLUDED_PRODUCT_SHELL_BEHAVIORS
    assert "full_product_tool_inventory" in EXCLUDED_PRODUCT_SHELL_BEHAVIORS


@pytest.mark.unit
def test_legacy_ownership_map_covers_current_runtime_surfaces() -> None:
    assert LEGACY_MASE_OWNERSHIP_MAP["services/agent-launcher/app/agent_fs.py"] == (
        "legacy_runtime_prompt_assembly"
    )
    assert LEGACY_MASE_OWNERSHIP_MAP["services/agent-launcher/app/heartbeat_runtime.py"] == (
        "legacy_inner_loop_and_observation_shaping"
    )

