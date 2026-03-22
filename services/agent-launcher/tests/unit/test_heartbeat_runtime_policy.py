"""Unit tests for heartbeat policy resolution, gating, and observations."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app.heartbeat_runtime import (
    MEMORY_MODE_LAST_N_TURNS,
    MEMORY_MODE_STATELESS,
    StopReason,
    build_turn_memory_message,
    evaluate_gating,
    normalize_observations,
    resolve_heartbeat_policy,
)


@pytest.mark.unit
def test_policy_resolution_uses_agent_config_overrides(tmp_path: Path) -> None:
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "config.json").write_text(
        json.dumps(
            {
                "heartbeat": {
                    "prompt": "Custom heartbeat prompt",
                    "max_model_calls_per_tick": 5,
                    "max_actions_per_tick": 12,
                    "max_tick_runtime_ms": 150000,
                    "skip_if_heartbeat_empty": True,
                    "memory_mode": "last_n_turns",
                    "memory_turn_window": 4,
                    "memory_max_chars": 2500,
                }
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_heartbeat_policy(agent_root)

    assert resolved.source == "agent_config"
    assert resolved.policy.prompt == "Custom heartbeat prompt"
    assert resolved.policy.max_model_calls_per_tick == 5
    assert resolved.policy.max_actions_per_tick == 12
    assert resolved.policy.max_tick_runtime_ms == 150000
    assert resolved.policy.skip_if_heartbeat_empty is True
    assert resolved.policy.memory_mode == MEMORY_MODE_LAST_N_TURNS
    assert resolved.policy.memory_turn_window == 4
    assert resolved.policy.memory_max_chars == 2500


@pytest.mark.unit
def test_policy_resolution_without_heartbeat_block_uses_defaults(tmp_path: Path) -> None:
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "config.json").write_text(
        json.dumps({"model": {"model": "openai/gpt-5-mini"}}),
        encoding="utf-8",
    )

    resolved = resolve_heartbeat_policy(agent_root)

    assert resolved.source == "defaults_no_heartbeat_block"
    assert resolved.policy.prompt == (
        "Read HEARTBEAT.md if it exists (workspace context). "
        "Follow it strictly. "
        "Do not infer or repeat old tasks from prior chats. "
        "If nothing needs attention, reply HEARTBEAT_OK."
    )
    assert resolved.policy.max_model_calls_per_tick == 3
    assert resolved.policy.max_actions_per_tick == 8
    assert resolved.policy.memory_mode == MEMORY_MODE_STATELESS


@pytest.mark.unit
def test_policy_resolution_malformed_config_returns_defaults(tmp_path: Path) -> None:
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "config.json").write_text("{not-json", encoding="utf-8")

    resolved = resolve_heartbeat_policy(agent_root)

    assert resolved.source == "defaults"
    assert resolved.policy.every == "5m"
    assert resolved.policy.max_tick_runtime_ms == 90000
    assert resolved.policy.memory_mode == MEMORY_MODE_STATELESS
    assert resolved.raw_policy == {}


@pytest.mark.unit
def test_policy_resolution_missing_config_returns_defaults(tmp_path: Path) -> None:
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True, exist_ok=True)

    resolved = resolve_heartbeat_policy(agent_root)

    assert resolved.source == "defaults"
    assert resolved.policy.every == "5m"
    assert resolved.policy.max_prompt_chars == 32000
    assert resolved.policy.memory_mode == MEMORY_MODE_STATELESS


@pytest.mark.unit
def test_policy_resolution_invalid_memory_mode_falls_back_to_stateless(tmp_path: Path) -> None:
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "config.json").write_text(
        json.dumps(
            {
                "heartbeat": {
                    "memory_mode": "full_transcript",
                    "memory_turn_window": 9,
                }
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_heartbeat_policy(agent_root)
    assert resolved.policy.memory_mode == MEMORY_MODE_STATELESS
    assert resolved.policy.memory_turn_window == 9


@pytest.mark.unit
def test_policy_resolution_zero_limits_disable_count_caps(tmp_path: Path) -> None:
    agent_root = tmp_path / "agent"
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "config.json").write_text(
        json.dumps(
            {
                "heartbeat": {
                    "max_model_calls_per_tick": 0,
                    "max_actions_per_tick": 0,
                }
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_heartbeat_policy(agent_root)

    assert resolved.policy.max_model_calls_per_tick is None
    assert resolved.policy.max_actions_per_tick is None


@pytest.mark.unit
def test_gating_skips_on_empty_heartbeat_when_policy_requires() -> None:
    agent_root = Path("/")
    resolved = resolve_heartbeat_policy(agent_root)
    policy = resolved.policy.__class__(
        **{
            **resolved.policy.__dict__,
            "skip_if_heartbeat_empty": True,
        }
    )

    decision = evaluate_gating(policy, heartbeat_content="", scheduler_status={})

    assert decision.should_skip is True
    assert decision.reason == StopReason.GATED_SKIP


@pytest.mark.unit
def test_gating_skips_when_outside_active_hours() -> None:
    now = datetime.now(timezone.utc)
    start_h = (now.hour + 1) % 24
    end_h = (now.hour + 2) % 24
    policy = resolve_heartbeat_policy(Path("/")).policy.__class__(
        **{
            **resolve_heartbeat_policy(Path("/")).policy.__dict__,
            "active_hours": {
                "start": f"{start_h:02d}:00",
                "end": f"{end_h:02d}:00",
            },
        }
    )

    decision = evaluate_gating(policy, heartbeat_content="# hb", scheduler_status={})

    assert decision.should_skip is True
    assert decision.reason == StopReason.GATED_SKIP
    assert decision.metadata.get("gate") == "active_hours"


@pytest.mark.unit
def test_gating_skips_on_backpressure() -> None:
    policy = resolve_heartbeat_policy(Path("/")).policy
    decision = evaluate_gating(
        policy,
        heartbeat_content="# hb",
        scheduler_status={"in_flight_tasks": 30, "max_parallel_agents": 10},
    )

    assert decision.should_skip is True
    assert decision.reason == StopReason.GATED_SKIP


@pytest.mark.unit
def test_gating_proceeds_when_empty_heartbeat_allowed() -> None:
    policy = resolve_heartbeat_policy(Path("/")).policy.__class__(
        **{
            **resolve_heartbeat_policy(Path("/")).policy.__dict__,
            "skip_if_heartbeat_empty": False,
        }
    )
    decision = evaluate_gating(policy, heartbeat_content="", scheduler_status={})
    assert decision.should_skip is False


@pytest.mark.unit
def test_observation_normalizer_strips_tokens_and_truncates() -> None:
    results = [
        {
            "action": "http",
            "action_type": "http_get",
            "action_name": "read_feed",
            "action_key": "GET:https://example/feed:read_feed",
            "success": False,
            "method": "GET",
            "path": "https://example/feed",
            "status_code": 500,
            "error": "Authorization: Bearer SUPERSECRET",
            "response": {
                "api_token": "TOPSECRET",
                "body": "x" * 100,
            },
        }
    ]

    observations = normalize_observations(results, max_chars=40)

    assert observations[0]["success"] is False
    assert "SUPERSECRET" not in observations[0].get("error", "")
    assert "TOPSECRET" not in observations[0].get("response_preview", "")
    assert observations[0].get("response_preview_truncated") is True


@pytest.mark.unit
def test_observation_normalizer_includes_http_success_shape() -> None:
    results = [
        {
            "action": "http",
            "action_type": "http_get",
            "action_name": "fetch_contract",
            "success": True,
            "method": "GET",
            "url": "https://example.test/contract",
            "path": "https://example.test/contract",
            "status_code": 200,
            "response": {"contract_version": "v1.0.0"},
        }
    ]

    observations = normalize_observations(results, max_chars=250)
    item = observations[0]
    assert item["action_type"] == "http_get"
    assert item["success"] is True
    assert item["status_code"] == 200
    assert item["method"] == "GET"
    assert item["url"] == "https://example.test/contract"
    assert "response_preview" in item


@pytest.mark.unit
def test_observation_normalizer_preserves_fs_operation() -> None:
    results = [
        {
            "action": "fs",
            "action_type": "fs_write",
            "action_name": "journal_append",
            "action_key": "fs:append:journal.md",
            "success": True,
            "operation": "append",
            "path": "journal.md",
        }
    ]

    observations = normalize_observations(results, max_chars=200)

    assert observations[0]["action_type"] == "fs_write"
    assert observations[0]["operation"] == "append"
    assert observations[0]["path"] == "journal.md"


@pytest.mark.unit
def test_observation_normalizer_heartbeat_ok_minimal_bundle() -> None:
    observations = normalize_observations(
        [{"action": "heartbeat_ok", "success": True}],
        max_chars=200,
    )
    item = observations[0]
    assert item["action_type"] == "heartbeat_ok"
    assert item["success"] is True
    assert "method" not in item
    assert "path" not in item
    assert "status_code" not in item


@pytest.mark.unit
def test_build_turn_memory_message_applies_bounded_budget() -> None:
    entries = [
        "## Heartbeat Update\n- **Status**: completed\n- **Summary**: run A",
        "## Heartbeat Update\n- **Status**: error\n- **Summary**: " + ("x" * 600),
    ]

    message = build_turn_memory_message(entries, max_chars=120)
    assert message is not None
    assert "memory context truncated" in message
    assert len(message) >= 120
