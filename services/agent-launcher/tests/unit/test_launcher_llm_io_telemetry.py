"""Unit tests for llm_io telemetry emission semantics."""

from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app.routes import launcher as launcher_module


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_llm_rounds_marks_parse_error_as_unsuccessful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    async def fake_record_action(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(launcher_module, "record_action", fake_record_action)

    await launcher_module._emit_llm_rounds(
        run_id="run-1",
        agent_id="agent-1",
        tick=3,
        heartbeat_index=1,
        environment_url="http://example-env",
        environment_name="testenv",
        resolved_model="openai/gpt-5-mini",
        policy_source="agent_config",
        agent_core="openclaw_py_core",
        base_user_message="heartbeat",
        system_prompt_parts=[],
        round_details=[
            {
                "round_index": 1,
                "memory_mode": "last_n_turns",
                "memory_turns_loaded": 2,
                "parse_error": "invalid json",
                "llm_error": None,
                "usage": {},
                "llm_cost_usd": 0.0,
                "llm_tokens_input": 0,
                "llm_tokens_output": 0,
            }
        ],
    )

    assert captured, "expected telemetry emission"
    assert captured[0]["success"] is False
    assert captured[0]["payload"]["memory_mode"] == "last_n_turns"
    assert captured[0]["payload"]["memory_turns_loaded"] == 2
    assert captured[0]["payload"]["agent_core"] == "openclaw_py_core"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_prompt_parts_uses_bounded_content_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    async def fake_record_action(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(launcher_module, "record_action", fake_record_action)
    launcher_module._PROMPT_PART_SENT.clear()

    long_content = "x" * (launcher_module.PROMPT_PART_CONTENT_PREVIEW_MAX_CHARS + 50)
    await launcher_module._emit_prompt_parts(
        run_id="run-1",
        agent_id="agent-1",
        tick=2,
        heartbeat_index=1,
        environment_url="http://env",
        environment_name="testenv",
        policy_source="agent_config",
        agent_core="openclaw_py_core",
        system_prompt_parts=[
            {
                "name": "AGENTS.md",
                "kind": "workspace_file",
                "dynamic": False,
                "sha256": "abc123",
                "bytes": len(long_content),
                "content": long_content,
                "truncated": False,
                "truncation_reason": None,
            }
        ],
    )

    assert captured, "expected prompt part telemetry emission"
    payload = captured[0]["payload"]
    assert payload["content"] == long_content
    assert payload["content_preview"] == long_content[: launcher_module.PROMPT_PART_CONTENT_PREVIEW_MAX_CHARS]
    assert payload["content_preview_truncated"] is True
    assert payload["agent_core"] == "openclaw_py_core"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_parse_error_rate_alarm_emits_when_threshold_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    async def fake_record_action(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(launcher_module, "record_action", fake_record_action)

    await launcher_module._emit_parse_error_rate_alarm(
        run_id="run-1",
        agent_id="agent-1",
        tick=12,
        heartbeat_index=3,
        environment_url="http://env",
        environment_name="moltbook",
        resolved_model="openai/gpt-5-mini",
        policy_source="agent_config",
        round_details=[
            {"round_index": 0, "parse_error": "invalid json"},
            {"round_index": 1},
            {"round_index": 2, "parse_error": "bad markdown wrapper"},
            {"round_index": 3},
        ],
        rate_threshold=0.25,
        min_rounds=3,
    )

    assert len(captured) == 1
    event = captured[0]
    assert event["action_type"] == launcher_module.PARSE_ERROR_ALARM_ACTION_TYPE
    assert event["success"] is False
    assert event["payload"]["parse_error_rounds"] == 2
    assert event["payload"]["total_rounds"] == 4
    assert event["payload"]["parse_error_rate"] == 0.5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_parse_error_rate_alarm_skips_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    async def fake_record_action(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(launcher_module, "record_action", fake_record_action)

    await launcher_module._emit_parse_error_rate_alarm(
        run_id="run-1",
        agent_id="agent-1",
        tick=13,
        heartbeat_index=4,
        environment_url="http://env",
        environment_name="moltbook",
        resolved_model="openai/gpt-5-mini",
        policy_source="agent_config",
        round_details=[
            {"round_index": 0},
            {"round_index": 1, "parse_error": "invalid json"},
            {"round_index": 2},
            {"round_index": 3},
        ],
        rate_threshold=0.5,
        min_rounds=3,
    )

    assert captured == []
