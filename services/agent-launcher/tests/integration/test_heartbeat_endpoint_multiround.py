"""Integration tests for heartbeat endpoint multi-round runtime behavior."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, ConfigDict
from typing import Optional


from _bootstrap import PROJECT_ROOT

# Ensure settings boot with non-network test defaults before app imports.
os.environ.setdefault("AGENT_LAUNCHER_LLM_PROVIDER", "dummy")
os.environ.setdefault("AGENT_LAUNCHER_OPENROUTER_API_KEY", "dummy")

import app.agent_fs as agent_fs_module
import app.executor as executor_module
import app.llm_client as llm_client_module
import app.scheduler as scheduler_module
from app.routes import launcher as launcher_module
from app.config import settings
from app.main import app
from app.scheduler import heartbeat_scheduler, SchedulerState


@pytest.fixture
def test_runtime_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    agents_root = tmp_path / "agents"
    agents_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))
    monkeypatch.setattr(settings, "llm_provider", "dummy")
    monkeypatch.setattr(settings, "openrouter_api_key", "dummy")
    monkeypatch.setattr(settings, "environment_url", "http://localhost:8005")
    monkeypatch.setattr(
        agent_fs_module,
        "RUNTIME_PACKAGE_PATH",
        PROJECT_ROOT / "runtimes",
    )
    monkeypatch.setattr(
        agent_fs_module,
        "ENVIRONMENT_PACKAGE_PATH",
        PROJECT_ROOT / "environments",
    )
    return agents_root


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_endpoint_multi_round_contract(test_runtime_paths: Path) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-multiround",
                "run_id": "run-multiround",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-multiround",
                "run_id": "run-multiround",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 2,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        payload = heartbeat.json()

    assert payload["status"] == "completed"
    assert payload["agent_core"] == "openclaw_py_core"
    assert payload["stop_reason"] == "heartbeat_ok"
    assert payload["rounds_executed"] == 3
    assert payload["model_calls"] == 3
    assert payload["actions_executed"] == 2
    methods = [str(item.get("method", "")).upper() for item in payload["results"]]
    assert "GET" in methods
    assert "POST" in methods
    assert len(payload["round_details"]) == 3
    assert payload["llm_cost"] == 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_autonomy_only_across_three_ticks(
    test_runtime_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_user_messages = []

    async def _autonomy_dummy_messages(self, messages, tools=None):
        assistant_turns = len([item for item in messages if item.get("role") == "assistant"])
        if assistant_turns == 0:
            last_user_message = next(
                (item.get("content", "") for item in reversed(messages) if item.get("role") == "user"),
                "",
            )
            initial_user_messages.append(str(last_user_message))
            return json.dumps(
                [
                    {
                        "action": "http",
                        "method": "GET",
                        "url": "http://localhost:8005/contract",
                        "description": "tick read",
                    }
                ]
            )
        if assistant_turns == 1:
            return json.dumps(
                [
                    {
                        "action": "http",
                        "method": "POST",
                        "url": "http://localhost:8005/api/v1/posts",
                        "body": {"content": "autonomy tick mutation"},
                        "description": "tick write",
                    }
                ]
            )
        return json.dumps([{"action": "heartbeat_ok"}])

    monkeypatch.setattr(
        llm_client_module.LLMClient,
        "_dummy_chat_completion_messages",
        _autonomy_dummy_messages,
    )

    payloads = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-autonomy",
                "run_id": "run-autonomy",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        config_path = test_runtime_paths / "agent-autonomy" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        heartbeat_cfg = config.get("heartbeat") if isinstance(config.get("heartbeat"), dict) else {}
        heartbeat_cfg["memory_mode"] = "stateless"
        config["heartbeat"] = heartbeat_cfg
        config_path.write_text(json.dumps(config), encoding="utf-8")

        for tick in (2, 3, 4):
            heartbeat = await client.post(
                "/heartbeat",
                json={
                    "agent_id": "agent-autonomy",
                    "run_id": "run-autonomy",
                    "environment_url": "http://localhost:8005",
                    "environment_name": "moltbook",
                    "tick": tick,
                },
            )
            assert heartbeat.status_code == 200, heartbeat.text
            payloads.append(heartbeat.json())

    assert len(payloads) == 3
    assert len(initial_user_messages) == 3
    assert all(str(item).strip() for item in initial_user_messages)
    for payload in payloads:
        assert payload["status"] == "completed"
        assert payload["stop_reason"] == "heartbeat_ok"
        assert payload["rounds_executed"] == 3
        assert payload["model_calls"] == 3
        assert payload["actions_executed"] == 2
        methods = {str(item.get("method", "")).upper() for item in payload["results"]}
        assert {"GET", "POST"}.issubset(methods)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduler_timeout_allows_turn_within_runtime_budget(
    test_runtime_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_async_client = httpx.AsyncClient

    async def _slow_dummy_messages(self, messages, tools=None):
        await asyncio.sleep(0.05)
        assistant_turns = len([item for item in messages if item.get("role") == "assistant"])
        if assistant_turns == 0:
            return json.dumps(
                [
                    {
                        "action": "http",
                        "method": "GET",
                        "url": "http://localhost:8005/contract",
                        "description": "slow read",
                    }
                ]
            )
        return json.dumps([{"action": "heartbeat_ok"}])

    monkeypatch.setattr(
        llm_client_module.LLMClient,
        "_dummy_chat_completion_messages",
        _slow_dummy_messages,
    )

    class _InProcessAsyncClient:
        def __init__(self, timeout=None, **kwargs):
            self._timeout = float(timeout) if timeout is not None else None
            self._inner = real_async_client(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return await self._inner.__aexit__(exc_type, exc, tb)

        async def post(self, url, **kwargs):
            path = httpx.URL(url).path or str(url)
            request_coro = self._inner.post(path, **kwargs)
            if self._timeout is None:
                return await request_coro
            try:
                return await asyncio.wait_for(request_coro, timeout=self._timeout)
            except asyncio.TimeoutError as exc:
                raise httpx.ReadTimeout("scheduler heartbeat timed out") from exc

    transport = httpx.ASGITransport(app=app)
    async with real_async_client(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-timeout-budget",
                "run_id": "run-timeout-budget",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

    config_path = test_runtime_paths / "agent-timeout-budget" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    heartbeat_cfg = config.get("heartbeat") if isinstance(config.get("heartbeat"), dict) else {}
    heartbeat_cfg["max_model_calls_per_tick"] = 2
    heartbeat_cfg["max_tick_runtime_ms"] = 300
    config["heartbeat"] = heartbeat_cfg
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(
        scheduler_module,
        "httpx",
        SimpleNamespace(AsyncClient=_InProcessAsyncClient),
    )

    previous_timeout = heartbeat_scheduler._heartbeat_timeout
    previous_tick_count = heartbeat_scheduler._tick_count
    try:
        heartbeat_scheduler._tick_count = 2
        heartbeat_scheduler._heartbeat_timeout = 0.5
        result = await heartbeat_scheduler._call_heartbeat_endpoint(
            agent_id="agent-timeout-budget",
            run_id="run-timeout-budget",
            environment_url="http://localhost:8005",
        )
    finally:
        heartbeat_scheduler._tick_count = previous_tick_count
        heartbeat_scheduler._heartbeat_timeout = previous_timeout

    assert result.success is True
    assert result.metadata.get("stop_reason") == "heartbeat_ok"
    assert int(result.metadata.get("rounds_executed") or 0) == 2
    assert int(result.metadata.get("elapsed_ms") or 0) <= 300


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_endpoint_round2_llm_error_preserves_prior_results(
    test_runtime_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _round2_failure_messages(self, messages, tools=None):
        assistant_turns = len([item for item in messages if item.get("role") == "assistant"])
        if assistant_turns == 0:
            return json.dumps(
                [
                    {
                        "action": "http",
                        "method": "GET",
                        "url": "http://localhost:8005/contract",
                        "description": "round0 read",
                    }
                ]
            )
        raise llm_client_module.LLMError("forced round2 provider failure", error_type="provider_error")

    monkeypatch.setattr(
        llm_client_module.LLMClient,
        "_dummy_chat_completion_messages",
        _round2_failure_messages,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-round2-failure",
                "run_id": "run-round2-failure",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-round2-failure",
                "run_id": "run-round2-failure",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 2,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        payload = heartbeat.json()

    assert payload["status"] == "error"
    assert payload["stop_reason"] == "error"
    assert payload["rounds_executed"] == 2
    assert payload["actions_executed"] == 1
    assert len(payload["results"]) == 1
    assert payload["error"] is not None
    assert "forced round2 provider failure" in str(payload["error"].get("message", ""))
    assert payload["round_details"][0]["stop_reason"] == "continue"
    assert payload["round_details"][1]["stop_reason"] == "error"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_endpoint_one_call_fallback_mode(test_runtime_paths: Path) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-onecall",
                "run_id": "run-onecall",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        config_path = test_runtime_paths / "agent-onecall" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        heartbeat_cfg = config.get("heartbeat") if isinstance(config.get("heartbeat"), dict) else {}
        heartbeat_cfg["max_model_calls_per_tick"] = 1
        config["heartbeat"] = heartbeat_cfg
        config_path.write_text(json.dumps(config), encoding="utf-8")

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-onecall",
                "run_id": "run-onecall",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 2,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        payload = heartbeat.json()

    assert payload["rounds_executed"] == 1
    assert payload["model_calls"] == 1
    assert payload["stop_reason"] == "max_model_calls_reached"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_endpoint_first_tick_is_agent_owned(test_runtime_paths: Path) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-bootstrap",
                "run_id": "run-bootstrap",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-bootstrap",
                "run_id": "run-bootstrap",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 1,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        payload = heartbeat.json()

    assert payload["status"] == "completed"
    assert payload["agent_core"] == "openclaw_py_core"
    assert payload["rounds_executed"] >= 1
    assert payload["model_calls"] >= 1
    assert all(detail.get("path") != "bootstrap_probe" for detail in payload["round_details"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_endpoint_missing_policy_block_falls_back_to_defaults(
    test_runtime_paths: Path,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-default-policy",
                "run_id": "run-default-policy",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        config_path = test_runtime_paths / "agent-default-policy" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("heartbeat", None)
        config_path.write_text(json.dumps(config), encoding="utf-8")

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-default-policy",
                "run_id": "run-default-policy",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 2,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        payload = heartbeat.json()

    assert payload["status"] == "completed"
    assert payload["rounds_executed"] >= 1
    assert payload["stop_reason"] in {
        "heartbeat_ok",
        "max_model_calls_reached",
        "no_actionable_actions",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_endpoint_emits_prompt_llm_and_action_telemetry(
    test_runtime_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted_action_types = []
    emitted_event_types = []

    async def _capture_record_action(*args, **kwargs):
        action_type = kwargs.get("action_type")
        if action_type:
            emitted_action_types.append(str(action_type))
        payload = kwargs.get("payload") or {}
        event_type = payload.get("event_type")
        if event_type:
            emitted_event_types.append(str(event_type))

    monkeypatch.setattr(launcher_module, "record_action", _capture_record_action)
    monkeypatch.setattr(executor_module, "record_action", _capture_record_action)

    async def _local_dummy_messages(self, messages, tools=None):
        assistant_turns = len([item for item in messages if item.get("role") == "assistant"])
        if assistant_turns == 0:
            return json.dumps(
                [
                    {
                        "action": "http",
                        "method": "GET",
                        "url": "http://localhost:8005/contract",
                        "description": "local contract read",
                    }
                ]
            )
        return json.dumps([{"action": "heartbeat_ok"}])

    monkeypatch.setattr(
        llm_client_module.LLMClient,
        "_dummy_chat_completion_messages",
        _local_dummy_messages,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-telemetry",
                "run_id": "run-telemetry",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-telemetry",
                "run_id": "run-telemetry",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 2,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text

    assert "prompt_part" in emitted_action_types
    assert "llm_io" in emitted_action_types
    assert "action_attempt" in emitted_event_types


class LegacyHeartbeatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    status: str
    actions_executed: int
    results: list
    summary: str
    llm_cost: float
    error: Optional[dict] = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_response_backward_compatible_for_legacy_consumers(
    test_runtime_paths: Path,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-legacy-parser",
                "run_id": "run-legacy-parser",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-legacy-parser",
                "run_id": "run-legacy-parser",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 2,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        payload = heartbeat.json()

    parsed = LegacyHeartbeatResponse.model_validate(payload)
    assert parsed.agent_id == "agent-legacy-parser"
    assert parsed.status in {"completed", "error", "skipped"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_scheduler_paused_returns_gated_skip(test_runtime_paths: Path) -> None:
    transport = httpx.ASGITransport(app=app)
    previous_state = heartbeat_scheduler._state
    heartbeat_scheduler._state = SchedulerState.PAUSED
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            create = await client.post(
                "/agents",
                json={
                    "agent_id": "agent-paused",
                    "run_id": "run-paused",
                    "runtime_id": "openclaw",
                    "environment_name": "moltbook",
                },
            )
            assert create.status_code == 200, create.text

            heartbeat = await client.post(
                "/heartbeat",
                json={
                    "agent_id": "agent-paused",
                    "run_id": "run-paused",
                    "environment_url": "http://localhost:8005",
                    "environment_name": "moltbook",
                    "agent_token": "test-token",
                    "tick": 2,
                },
            )
            assert heartbeat.status_code == 200, heartbeat.text
            payload = heartbeat.json()
    finally:
        heartbeat_scheduler._state = previous_state

    assert payload["status"] == "paused"
    assert payload["stop_reason"] == "gated_skip"
    assert payload["prompt_contract_version"] == launcher_module.PROMPT_CONTRACT_VERSION
    assert payload["interaction_mode"] == launcher_module.INTERACTION_MODE
    assert payload["policy_source"] == "scheduler_paused"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_policy_skip_includes_policy_and_prompt_contract_metadata(
    test_runtime_paths: Path,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/agents",
            json={
                "agent_id": "agent-policy-skip",
                "run_id": "run-policy-skip",
                "runtime_id": "openclaw",
                "environment_name": "moltbook",
            },
        )
        assert create.status_code == 200, create.text

        config_path = test_runtime_paths / "agent-policy-skip" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        heartbeat_cfg = config.get("heartbeat") if isinstance(config.get("heartbeat"), dict) else {}
        heartbeat_cfg["enabled"] = False
        config["heartbeat"] = heartbeat_cfg
        config_path.write_text(json.dumps(config), encoding="utf-8")

        heartbeat = await client.post(
            "/heartbeat",
            json={
                "agent_id": "agent-policy-skip",
                "run_id": "run-policy-skip",
                "environment_url": "http://localhost:8005",
                "environment_name": "moltbook",
                "agent_token": "test-token",
                "tick": 3,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        payload = heartbeat.json()

    assert payload["status"] == "skipped"
    assert payload["stop_reason"] == "gated_skip"
    assert payload["prompt_contract_version"] == launcher_module.PROMPT_CONTRACT_VERSION
    assert payload["interaction_mode"] == launcher_module.INTERACTION_MODE
    assert payload["policy_source"] == "agent_config"
    assert isinstance(payload.get("round_details"), list) and payload["round_details"]
    first_round = payload["round_details"][0]
    assert first_round.get("prompt_contract_version") == launcher_module.PROMPT_CONTRACT_VERSION
    assert first_round.get("interaction_mode") == launcher_module.INTERACTION_MODE
    assert first_round.get("policy_source") == "agent_config"
