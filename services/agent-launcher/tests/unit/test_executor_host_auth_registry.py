"""Unit tests for host-scoped auth registry behavior in ActionExecutor."""

import types
from pathlib import Path
import sys

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

from app.action_parser import Action, ActionType, HTTPMethod
from app.agent_fs import AgentFilesystem
from app.executor import ActionExecutor
from app.config import settings


class _FakeResponse:
    def __init__(self, *, status_code: int, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = payload if isinstance(payload, str) else str(payload)

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("Not a JSON body")


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, *, method, url, headers=None, json=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "json": json,
            }
        )
        if not self.responses:
            raise AssertionError("No fake response available")
        return self.responses.pop(0)

    async def aclose(self):
        return None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mutating_secondary_write_fails_before_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    executor = ActionExecutor(
        AgentFilesystem("agent-1"),
        agent_id="agent-1",
        run_id="run-1",
        primary_environment_url="http://primary-env:8000",
        allowed_environment_urls=["http://secondary-env:8000"],
        agent_token="primary-token",
    )
    fake_client = _FakeAsyncClient([])
    executor.http_client = fake_client

    action = Action(
        action=ActionType.HTTP,
        action_name="write_secondary",
        method=HTTPMethod.POST,
        url="http://secondary-env:8000/posts",
        body={"title": "x"},
    )
    result = await executor.execute_action(action)

    assert result["success"] is False
    assert result["error_code"] == "unregistered_secondary_environment"
    assert fake_client.calls == []
    await executor.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registration_persists_secondary_token_and_routing_is_host_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    executor = ActionExecutor(
        AgentFilesystem("agent-1"),
        agent_id="agent-1",
        run_id="run-1",
        primary_environment_url="http://primary-env:8000",
        allowed_environment_urls=["http://secondary-env:8000"],
        agent_token="primary-token",
    )
    fake_client = _FakeAsyncClient(
        [
            _FakeResponse(
                status_code=200,
                payload={"agent_id": "agent-1", "api_token": "secondary-token"},
                headers={},
            ),
            _FakeResponse(status_code=201, payload={"ok": True}, headers={}),
            _FakeResponse(status_code=201, payload={"ok": True}, headers={}),
        ]
    )
    executor.http_client = fake_client

    register_action = Action(
        action=ActionType.HTTP,
        action_name="register_secondary",
        method=HTTPMethod.POST,
        url="http://secondary-env:8000/auth/register",
        body={"agent_id": "agent-1"},
    )
    secondary_write_action = Action(
        action=ActionType.HTTP,
        action_name="write_secondary",
        method=HTTPMethod.POST,
        url="http://secondary-env:8000/posts",
        body={"title": "secondary"},
    )
    primary_write_action = Action(
        action=ActionType.HTTP,
        action_name="write_primary",
        method=HTTPMethod.POST,
        url="http://primary-env:8000/posts",
        body={"title": "primary"},
    )

    register_result = await executor.execute_action(register_action)
    secondary_result = await executor.execute_action(secondary_write_action)
    primary_result = await executor.execute_action(primary_write_action)

    assert register_result["success"] is True
    assert register_result["auth_registry_updated"] is True
    assert secondary_result["success"] is True
    assert primary_result["success"] is True

    assert len(fake_client.calls) == 3
    register_call = fake_client.calls[0]
    secondary_call = fake_client.calls[1]
    primary_call = fake_client.calls[2]

    assert "Authorization" not in register_call["headers"]
    assert secondary_call["headers"]["Authorization"] == "Bearer secondary-token"
    assert primary_call["headers"]["Authorization"] == "Bearer primary-token"
    await executor.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_relative_request_path_resolves_against_primary_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    executor = ActionExecutor(
        AgentFilesystem("agent-1"),
        agent_id="agent-1",
        run_id="run-1",
        primary_environment_url="http://primary-env:8000",
        agent_token="primary-token",
    )
    fake_client = _FakeAsyncClient(
        [
            _FakeResponse(status_code=200, payload={"ok": True}, headers={}),
        ]
    )
    executor.http_client = fake_client

    action = Action(
        action=ActionType.HTTP,
        action_name="read_relative_contract",
        method=HTTPMethod.GET,
        url="/contract",
    )

    result = await executor.execute_action(action)

    assert result["success"] is True
    assert fake_client.calls[0]["url"] == "http://primary-env:8000/contract"
    await executor.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_supplied_authorization_header_is_preserved_for_allowed_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    executor = ActionExecutor(
        AgentFilesystem("agent-1"),
        agent_id="agent-1",
        run_id="run-1",
        primary_environment_url="http://primary-env:8000",
    )
    fake_client = _FakeAsyncClient(
        [
            _FakeResponse(status_code=201, payload={"ok": True}, headers={}),
        ]
    )
    executor.http_client = fake_client

    action = Action(
        action=ActionType.HTTP,
        action_name="write_with_explicit_auth",
        method=HTTPMethod.POST,
        url="http://primary-env:8000/posts",
        headers={"Authorization": "Bearer explicit-token"},
        body={"title": "explicit"},
    )

    result = await executor.execute_action(action)

    assert result["success"] is True
    assert fake_client.calls[0]["headers"]["Authorization"] == "Bearer explicit-token"
    await executor.close()
