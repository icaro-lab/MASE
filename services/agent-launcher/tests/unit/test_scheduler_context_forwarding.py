"""Unit tests for scheduler heartbeat context forwarding."""

import asyncio
import types
from pathlib import Path
import sys
from typing import Optional

import pytest


from _bootstrap import PROJECT_ROOT


# Keep unit tests independent from optional apscheduler dependency.
if "apscheduler.schedulers.asyncio" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    apscheduler_schedulers_module = types.ModuleType("apscheduler.schedulers")
    apscheduler_asyncio_module = types.ModuleType("apscheduler.schedulers.asyncio")
    apscheduler_triggers_module = types.ModuleType("apscheduler.triggers")
    apscheduler_date_module = types.ModuleType("apscheduler.triggers.date")

    class _DummyAsyncIOScheduler:
        def start(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            return None

        def add_job(self, *args, **kwargs) -> None:
            return None

        def remove_job(self, *args, **kwargs) -> None:
            return None

    class _DummyDateTrigger:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    apscheduler_asyncio_module.AsyncIOScheduler = _DummyAsyncIOScheduler
    apscheduler_date_module.DateTrigger = _DummyDateTrigger

    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.schedulers"] = apscheduler_schedulers_module
    sys.modules["apscheduler.schedulers.asyncio"] = apscheduler_asyncio_module
    sys.modules["apscheduler.triggers"] = apscheduler_triggers_module
    sys.modules["apscheduler.triggers.date"] = apscheduler_date_module

from app import scheduler as scheduler_module
from app.scheduler import AgentState, HeartbeatScheduler


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, captured_requests, timeout: float, responses=None):
        self._captured_requests = captured_requests
        self._timeout = timeout
        self._responses = responses if responses is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json):
        self._captured_requests.append(
            {
                "url": url,
                "json": json,
                "timeout": self._timeout,
            }
        )
        if self._responses:
            return _FakeResponse(self._responses.pop(0))
        return _FakeResponse(
            {
                "status": "completed",
                "actions_executed": 1,
                "llm_cost": 0.0,
                "round_details": [],
            }
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scheduler_forwards_allowed_urls_without_auth_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="5s",
        timeout="2s",
        environment_url="http://primary-env:8000",
        environment_name="moltbook",
        allowed_environment_urls=["http://secondary-env:8000"],
    )
    scheduler._tick_count = 7

    captured = []
    monkeypatch.setattr(
        scheduler_module.httpx,
        "AsyncClient",
        lambda timeout=2.0: _FakeAsyncClient(captured, timeout=timeout),
    )

    result = await scheduler._call_heartbeat_endpoint(
        agent_id="agent-1",
        run_id="run-1",
        environment_url="http://primary-env:8000",
    )

    assert result.success is True
    assert len(captured) == 1
    payload = captured[0]["json"]
    assert payload["allowed_environment_urls"] == ["http://secondary-env:8000"]
    assert "agent_token" not in payload
    assert "environment_auth_registry_seed" not in payload
    assert payload["tick"] == 7


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scheduler_does_not_seed_registration_token_across_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="5s",
        timeout="2s",
        environment_url="http://primary-env:8000",
        environment_name="moltbook",
        allowed_environment_urls=["http://secondary-env:8000"],
    )

    captured = []
    responses = [
        {
            "status": "completed",
            "actions_executed": 1,
            "llm_cost": 0.0,
            "results": [
                {
                    "action": "http",
                    "success": True,
                    "method": "POST",
                    "path": "http://secondary-env:8000/auth/register",
                    "response": {
                        "body": {
                            "agent_id": "agent-1",
                            "api_token": "token-secondary",
                        }
                    },
                }
            ],
            "round_details": [],
        },
        {
            "status": "completed",
            "actions_executed": 1,
            "llm_cost": 0.0,
            "round_details": [],
        },
    ]
    monkeypatch.setattr(
        scheduler_module.httpx,
        "AsyncClient",
        lambda timeout=2.0: _FakeAsyncClient(captured, timeout=timeout, responses=responses),
    )

    await scheduler._call_heartbeat_endpoint(
        agent_id="agent-1",
        run_id="run-1",
        environment_url="http://primary-env:8000",
    )
    await scheduler._call_heartbeat_endpoint(
        agent_id="agent-1",
        run_id="run-1",
        environment_url="http://primary-env:8000",
    )

    assert len(captured) == 2
    assert "agent_token" not in captured[1]["json"]
    assert "environment_auth_registry_seed" not in captured[1]["json"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scheduler_uses_agent_specific_model_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="5s",
        timeout="2s",
        model="openai/gpt-5-mini",
        environment_url="http://primary-env:8000",
        environment_name="moltbook",
        agent_models={"agent-1": "openai/gpt-5"},
    )
    scheduler._tick_count = 1

    captured = []
    monkeypatch.setattr(
        scheduler_module.httpx,
        "AsyncClient",
        lambda timeout=2.0: _FakeAsyncClient(captured, timeout=timeout),
    )

    await scheduler._call_heartbeat_endpoint(
        agent_id="agent-1",
        run_id="run-1",
        environment_url="http://primary-env:8000",
    )

    assert len(captured) == 1
    assert captured[0]["json"]["model"] == "openai/gpt-5"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scheduler_status_counts_only_executing_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="5s",
        timeout="2s",
        max_parallel=1,
        environment_url="http://primary-env:8000",
        environment_name="moltbook",
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_call(
        *,
        agent_id: str,
        run_id: str,
        environment_url: Optional[str] = None,
        heartbeat_index: Optional[int] = None,
    ):
        started.set()
        await release.wait()
        return scheduler_module.HeartbeatResult(
            agent_id=agent_id,
            success=True,
            actions_executed=1,
        )

    async def _fake_report(*args, **kwargs):
        return None

    monkeypatch.setattr(scheduler, "_call_heartbeat_endpoint", _fake_call)
    monkeypatch.setattr(scheduler, "_report_heartbeat_to_controller", _fake_report)
    scheduler._state = scheduler_module.SchedulerState.RUNNING

    tasks = [
        asyncio.create_task(scheduler._run_single_heartbeat(f"agent-{idx}", "run-1", 0.0))
        for idx in range(1, 4)
    ]
    await started.wait()

    status = scheduler.get_status()
    assert status["config"]["in_flight_tasks"] == 1

    release.set()
    await asyncio.gather(*tasks)

    assert scheduler.get_status()["config"]["in_flight_tasks"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scheduler_tracks_per_agent_heartbeat_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="5s",
        timeout="2s",
        environment_url="http://primary-env:8000",
        environment_name="moltbook",
    )
    scheduler._state = scheduler_module.SchedulerState.RUNNING

    captured = []

    async def _fake_call(*, agent_id: str, run_id: str, environment_url: Optional[str] = None, heartbeat_index: Optional[int] = None):
        captured.append(
            {
                "agent_id": agent_id,
                "run_id": run_id,
                "heartbeat_index": heartbeat_index,
            }
        )
        return scheduler_module.HeartbeatResult(
            agent_id=agent_id,
            success=True,
            actions_executed=1,
            metadata={"heartbeat_index": heartbeat_index or 0},
        )

    async def _fake_report(*args, **kwargs):
        return None

    monkeypatch.setattr(scheduler, "_call_heartbeat_endpoint", _fake_call)
    monkeypatch.setattr(scheduler, "_report_heartbeat_to_controller", _fake_report)

    await scheduler._run_single_heartbeat("agent-1", "run-1", 0.0)
    await scheduler._run_single_heartbeat("agent-1", "run-1", 0.0)
    await scheduler._run_single_heartbeat("agent-2", "run-1", 0.0)

    assert captured == [
        {"agent_id": "agent-1", "run_id": "run-1", "heartbeat_index": 1},
        {"agent_id": "agent-1", "run_id": "run-1", "heartbeat_index": 2},
        {"agent_id": "agent-2", "run_id": "run-1", "heartbeat_index": 1},
    ]

    status = scheduler.get_status()
    agent_states = {
        (row["run_id"], row["agent_id"]): row["heartbeat_index"]
        for row in status["agent_states"]
    }
    assert agent_states[("run-1", "agent-1")] == 2
    assert agent_states[("run-1", "agent-2")] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_loop_stops_at_max_heartbeats_per_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="5s",
        timeout="2s",
        max_heartbeats_per_agent=2,
        environment_url="http://primary-env:8000",
        environment_name="moltbook",
    )
    scheduler._state = scheduler_module.SchedulerState.RUNNING

    captured: list[int] = []

    async def _fake_call(*, agent_id: str, run_id: str, environment_url: Optional[str] = None, heartbeat_index: Optional[int] = None):
        captured.append(int(heartbeat_index or 0))
        return scheduler_module.HeartbeatResult(
            agent_id=agent_id,
            success=True,
            actions_executed=1,
            metadata={"heartbeat_index": heartbeat_index or 0},
        )

    async def _fake_report(*args, **kwargs):
        return None

    monkeypatch.setattr(scheduler, "_call_heartbeat_endpoint", _fake_call)
    monkeypatch.setattr(scheduler, "_report_heartbeat_to_controller", _fake_report)
    monkeypatch.setattr(scheduler, "_sample_next_tick_interval_seconds", lambda: 0.0)

    await asyncio.wait_for(
        scheduler._agent_loop(
            state_key="run-1/agent-1",
            agent_id="agent-1",
            run_id="run-1",
            initial_delay=0.0,
        ),
        timeout=0.5,
    )

    assert captured == [1, 2]
    status = scheduler.get_status()
    agent_states = {
        (row["run_id"], row["agent_id"]): row
        for row in status["agent_states"]
    }
    assert agent_states[("run-1", "agent-1")]["heartbeat_index"] == 2
    assert agent_states[("run-1", "agent-1")]["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_does_not_respawn_capped_agent_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="5s",
        timeout="2s",
        max_heartbeats_per_agent=2,
        environment_url="http://primary-env:8000",
        environment_name="moltbook",
    )
    scheduler._state = scheduler_module.SchedulerState.RUNNING
    scheduler._discover_agents = lambda: [("agent-1", "run-1")]  # type: ignore[method-assign]
    scheduler._agent_states["run-1/agent-1"] = scheduler_module.AgentState(
        agent_id="agent-1",
        run_id="run-1",
        heartbeat_index=2,
    )

    created_tasks: list[tuple[str, str]] = []

    async def _unexpected_loop(**kwargs):
        created_tasks.append((kwargs["run_id"], kwargs["agent_id"]))

    monkeypatch.setattr(scheduler, "_agent_loop", _unexpected_loop)

    await scheduler._sync_agents_once(initial_bootstrap=False)

    assert created_tasks == []
    assert scheduler._agent_loop_tasks == {}
    assert scheduler._agent_states["run-1/agent-1"].status == "completed"


@pytest.mark.unit
def test_scheduler_progress_summarizes_agent_state() -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(interval="5s", timeout="2s")
    scheduler._state = scheduler_module.SchedulerState.RUNNING
    scheduler._tick_count = 12
    scheduler._agent_states = {
        "run-1/agent-1": scheduler_module.AgentState(
            agent_id="agent-1",
            run_id="run-1",
            heartbeat_index=3,
            status="completed",
        ),
        "run-1/agent-2": scheduler_module.AgentState(
            agent_id="agent-2",
            run_id="run-1",
            heartbeat_index=1,
            status="running",
        ),
        "run-1/agent-3": scheduler_module.AgentState(
            agent_id="agent-3",
            run_id="run-1",
            heartbeat_index=2,
            status="failed",
            consecutive_failures=1,
        ),
    }

    progress = scheduler.get_progress()

    assert progress["tick_count"] == 12
    assert progress["agents"] == {
        "total": 3,
        "active": 1,
        "completed": 1,
        "failed": 1,
    }
    assert progress["progress"] == {
        "min_heartbeat_index": 1,
        "max_heartbeat_index": 3,
        "completed_agents": 1,
        "active_agents": 1,
        "failed_agents": 1,
        "total_agents": 3,
        "all_agents_terminal": False,
    }


@pytest.mark.unit
def test_update_agent_state_isolated_by_run_id() -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(interval="5s", timeout="2s")
    scheduler._agent_states = {
        "run-a/agent-1": AgentState(agent_id="agent-1", run_id="run-a", heartbeat_index=0, status="running"),
        "run-b/agent-1": AgentState(agent_id="agent-1", run_id="run-b", heartbeat_index=0, status="running"),
    }

    scheduler._update_agent_state(
        scheduler_module.HeartbeatResult(
            agent_id="agent-1",
            run_id="run-b",
            success=True,
            actions_executed=2,
        )
    )

    assert scheduler._agent_states["run-a/agent-1"].status == "running"
    assert scheduler._agent_states["run-a/agent-1"].total_actions == 0
    assert scheduler._agent_states["run-b/agent-1"].status == "active"
    assert scheduler._agent_states["run-b/agent-1"].total_actions == 2
