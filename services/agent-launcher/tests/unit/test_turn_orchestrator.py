"""Unit tests for bounded multi-call TurnOrchestrator behavior."""

import json
from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app.heartbeat_runtime import HeartbeatPolicy, StopReason, TurnOrchestrator
from app.openclaw_core.loop import OPENCLAW_CORE_LLM_TOOLS
from app.llm_client import LLMError


def _tool_call(name: str, args: dict) -> str:
    return f"{name}({json.dumps(args, separators=(',', ':'))})"


class StubLLMClient:
    def __init__(self, responses, costs=None, usages=None):
        self._responses = list(responses)
        self._costs = list(costs or [0.0] * len(self._responses))
        self._usages = list(
            usages
            or [
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "total_cost": 0.001,
                }
                for _ in self._responses
            ]
        )
        self._index = 0
        self._message_history = []
        self._last_usage = {}
        self._tool_history = []

    async def chat_completion_messages(self, messages, tools=None, tool_choice=None):
        if self._index >= len(self._responses):
            raise AssertionError("Unexpected extra round")
        self._message_history.append([dict(item) for item in messages])
        self._tool_history.append(
            {
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        item = self._responses[self._index]
        cost = self._costs[self._index]
        self._last_usage = dict(self._usages[self._index])
        self._index += 1
        if isinstance(item, Exception):
            raise item
        return item, cost

    def get_last_usage(self):
        return dict(self._last_usage)


class StubExecutor:
    def __init__(self, *, force_failure=False, sleep_seconds: float = 0.0):
        self.calls = 0
        self.force_failure = force_failure
        self.sleep_seconds = sleep_seconds

    async def execute_actions(self, actions, max_actions=None):
        if self.sleep_seconds > 0:
            import asyncio

            await asyncio.sleep(self.sleep_seconds)
        self.calls += 1
        items = list(actions)[: max_actions or len(actions)]
        results = []
        for idx, action in enumerate(items):
            results.append(
                {
                    "action": "http",
                    "action_type": "http_get",
                    "action_name": f"action_{self.calls}_{idx}",
                    "action_key": f"k_{self.calls}_{idx}",
                    "success": not self.force_failure,
                    "method": "GET",
                    "path": getattr(action, "url", "https://example"),
                    "status_code": 500 if self.force_failure else 200,
                    "error": "server failure" if self.force_failure else None,
                    "response": {"ok": not self.force_failure},
                }
            )
        return results

    async def close(self):
        return None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_two_rounds_stops_on_heartbeat_ok() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/feed"}),
            "HEARTBEAT_OK",
        ],
        costs=[0.01, 0.02],
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.HEARTBEAT_OK
    assert result.rounds_executed == 2
    assert result.model_calls == 2
    assert result.actions_executed == 1
    assert result.llm_cost == pytest.approx(0.03, rel=1e-6)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_single_round_heartbeat_ok() -> None:
    llm = StubLLMClient(responses=["HEARTBEAT_OK"], costs=[0.01])
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.HEARTBEAT_OK
    assert result.rounds_executed == 1
    assert result.model_calls == 1
    assert result.actions_executed == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_stops_on_max_model_calls() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/1"}),
            _tool_call("request", {"method": "GET", "url": "https://example/2"}),
        ]
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=2, max_actions_per_round=2, max_actions_per_tick=10)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.MAX_MODEL_CALLS_REACHED
    assert result.rounds_executed == 2
    assert result.actions_executed == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_stops_on_max_actions_reached() -> None:
    llm = StubLLMClient(
        responses=[
            (
                _tool_call("request", {"method": "GET", "url": "https://example/1"})
                + "\n"
                + _tool_call("request", {"method": "GET", "url": "https://example/2"})
            ),
            "HEARTBEAT_OK",
        ]
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=4, max_actions_per_tick=2)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.MAX_ACTIONS_REACHED
    assert result.rounds_executed == 1
    assert result.actions_executed == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_stops_on_max_tick_runtime_reached() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/1"}),
            "HEARTBEAT_OK",
        ]
    )
    executor = StubExecutor(sleep_seconds=0.02)
    policy = HeartbeatPolicy(
        max_model_calls_per_tick=3,
        max_actions_per_round=3,
        max_actions_per_tick=8,
        max_tick_runtime_ms=1,
    )

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.MAX_TICK_RUNTIME_REACHED
    assert result.rounds_executed == 1
    assert result.actions_executed == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_no_actionable_actions_text_only() -> None:
    llm = StubLLMClient(responses=["plain text with no direct tool call"])
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.NO_ACTIONABLE_ACTIONS
    assert result.actions_executed == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_injects_turn_memory_message() -> None:
    llm = StubLLMClient(responses=["HEARTBEAT_OK"])
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=1, max_actions_per_round=1, max_actions_per_tick=1)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=7,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
        memory_message="Recent turn memory from prior heartbeats:\n[memory 1]\nstatus=completed",
        memory_mode="last_n_turns",
        memory_turns_loaded=1,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.HEARTBEAT_OK
    first_round_messages = llm._message_history[0]
    assert first_round_messages[0]["role"] == "system"
    assert "Recent turn memory" in first_round_messages[1]["content"]
    assert first_round_messages[2]["content"] == "heartbeat"
    assert result.round_details[0]["memory_mode"] == "last_n_turns"
    assert result.round_details[0]["memory_turns_loaded"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_round0_llm_error_uses_fallback() -> None:
    llm = StubLLMClient(responses=[LLMError("provider unavailable", error_type="provider_error")])
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    async def _fallback(reason, llm_response, error_message):
        assert reason == "llm_error"
        assert llm_response is None
        assert "provider unavailable" in str(error_message)
        return {
            "status": "error",
            "stop_reason": StopReason.ERROR,
            "summary": "fallback path used",
            "results": [{"action": "http", "success": False}],
        }

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
        round_zero_fallback=_fallback,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.ERROR
    assert result.actions_executed == 1
    assert result.error is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_preserves_partial_results_on_round2_llm_error() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/feed"}),
            LLMError("rate limited", error_type="rate_limit"),
        ]
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.ERROR
    assert result.actions_executed == 1
    assert result.rounds_executed == 2
    assert result.error is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_action_failure_becomes_next_round_observation() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/fail"}),
            "HEARTBEAT_OK",
        ]
    )
    executor = StubExecutor(force_failure=True)
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.rounds_executed == 2
    assert result.stop_reason == StopReason.HEARTBEAT_OK
    second_call_messages = llm._message_history[1]
    last_user_message = second_call_messages[-1]["content"]
    assert "failure" in last_user_message.lower()
    assert "status code" in last_user_message.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_round2_parse_error_ends_as_no_actionable_actions() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/feed"}),
            "not-json",
        ]
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.status == "completed"
    assert result.stop_reason == StopReason.NO_ACTIONABLE_ACTIONS
    assert result.actions_executed == 1
    assert result.rounds_executed == 2
    assert result.error is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_aggregates_cost_and_tokens_across_rounds() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/1"}),
            _tool_call("request", {"method": "GET", "url": "https://example/2"}),
            "HEARTBEAT_OK",
        ],
        costs=[0.01, 0.02, 0.03],
        usages=[
            {"prompt_tokens": 11, "completion_tokens": 7, "total_cost": 0.01},
            {"prompt_tokens": 13, "completion_tokens": 5, "total_cost": 0.02},
            {"prompt_tokens": 17, "completion_tokens": 3, "total_cost": 0.03},
        ],
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.llm_cost == pytest.approx(0.06, rel=1e-6)
    assert result.total_tokens_input == 41
    assert result.total_tokens_output == 15


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_messages_grow_with_observation_context() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "GET", "url": "https://example/feed"}),
            "HEARTBEAT_OK",
        ],
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.rounds_executed == 2
    assert len(llm._message_history[0]) == 2
    assert len(llm._message_history[1]) == 4
    assert llm._message_history[1][0]["role"] == "system"
    assert llm._message_history[1][1]["role"] == "user"
    assert llm._message_history[1][2]["role"] == "assistant"
    assert llm._message_history[1][3]["role"] == "user"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_keeps_registration_token_in_model_context_but_redacts_round_details() -> None:
    llm = StubLLMClient(
        responses=[
            _tool_call("request", {"method": "POST", "url": "https://example/auth/register"}),
            "HEARTBEAT_OK",
        ],
    )

    class _TokenExecutor(StubExecutor):
        async def execute_actions(self, actions, max_actions=None):
            self.calls += 1
            return [
                {
                    "action": "http",
                    "action_type": "http_post",
                    "action_name": "register",
                    "action_key": "register",
                    "success": True,
                    "method": "POST",
                    "path": "https://example/auth/register",
                    "status_code": 201,
                    "response": {"agent_id": "agent-1", "api_token": "secret-token"},
                }
            ]

    executor = _TokenExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=3, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    second_call_messages = llm._message_history[1]
    last_user_message = second_call_messages[-1]["content"]
    assert "secret-token" in last_user_message
    assert "secret-token" not in result.round_details[0]["observation_message"]
    assert "secret-token" not in result.round_details[0]["observations"][0]["response_preview"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_advertises_native_tools_with_structured_body_schema() -> None:
    request_tool = next(item for item in OPENCLAW_CORE_LLM_TOOLS if item["function"]["name"] == "request")
    properties = request_tool["function"]["parameters"]["properties"]

    assert properties["json"]["type"] == "object"
    assert properties["body"]["type"] == "string"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_one_call_mode_matches_single_call_contract() -> None:
    llm = StubLLMClient(
        responses=[_tool_call("request", {"method": "GET", "url": "https://example/only"})],
    )
    executor = StubExecutor()
    policy = HeartbeatPolicy(max_model_calls_per_tick=1, max_actions_per_round=3, max_actions_per_tick=8)

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.rounds_executed == 1
    assert result.model_calls == 1
    assert result.actions_executed == 1
    assert result.stop_reason == StopReason.MAX_MODEL_CALLS_REACHED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_turn_orchestrator_openclaw_core_path_supplies_native_tools() -> None:
    llm = StubLLMClient(responses=["HEARTBEAT_OK"])
    executor = StubExecutor()
    policy = HeartbeatPolicy(
        max_model_calls_per_tick=1,
        max_actions_per_round=1,
        max_actions_per_tick=1,
        agent_core="openclaw_py_core",
    )

    orchestrator = TurnOrchestrator(
        agent_id="agent-1",
        run_id="run-1",
        tick=1,
        policy=policy,
        system_prompt="system",
        user_prompt="heartbeat",
        llm_client=llm,
        executor=executor,
    )
    result = await orchestrator.run()

    assert result.stop_reason == StopReason.HEARTBEAT_OK
    assert llm._tool_history[0]["tool_choice"] == "auto"
    assert llm._tool_history[0]["tools"] == OPENCLAW_CORE_LLM_TOOLS
