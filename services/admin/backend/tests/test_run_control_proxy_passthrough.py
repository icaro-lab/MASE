"""Unit tests for admin runtime/environment/run proxy behavior."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException


PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from services.admin.backend.app.orchestrator_client import ControllerProxyError, ControllerClient
from services.admin.backend.app.routes import v1


def _run(awaitable):
    return asyncio.run(awaitable)


class _Settings:
    controller_url = "http://controller:8002"
    http_timeout = 30


class _RaisingController:
    def __init__(self, method_name: str, exc: Exception) -> None:
        self.method_name = method_name
        self.exc = exc
        self.closed = False

    async def stop_run(self, run_id: str) -> dict[str, Any]:
        if self.method_name == "stop_run":
            raise self.exc
        return {"status": "noop"}

    async def restart_run_stack(self, run_id: str) -> dict[str, Any]:
        if self.method_name == "restart_run_stack":
            raise self.exc
        return {"status": "noop"}

    async def pause_run(self, run_id: str) -> dict[str, Any]:
        if self.method_name == "pause_run":
            raise self.exc
        return {"status": "noop"}

    async def resume_run(self, run_id: str) -> dict[str, Any]:
        if self.method_name == "resume_run":
            raise self.exc
        return {"status": "noop"}

    async def get_run_condition_manifest(self, run_id: str, mode: str = "strict") -> dict[str, Any]:
        if self.method_name == "get_run_condition_manifest":
            raise self.exc
        return {"run_id": run_id, "source": "native"}

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("handler", "method_name"),
    [
        (v1.stop_run, "stop_run"),
        (v1.restart_run_stack, "restart_run_stack"),
        (v1.pause_run, "pause_run"),
        (v1.resume_run, "resume_run"),
    ],
)
def test_run_control_routes_preserve_proxy_status(handler, method_name: str) -> None:
    controller = _RaisingController(method_name, ControllerProxyError(404, "not found"))

    with pytest.raises(HTTPException) as exc_info:
        _run(handler("run-1", controller=controller))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "not found"
    assert controller.closed is True


def test_stop_run_uses_extended_timeout_and_encoded_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.stop_run("run/needs encoding"))

    assert result["status"] == "ok"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/runs/run%2Fneeds%20encoding/stop"
    assert captured["kwargs"]["timeout"] == ControllerClient.RUN_CONTROL_TIMEOUT_SECONDS


def test_restart_run_stack_uses_extended_timeout_and_encoded_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {"status": "ok"}

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.restart_run_stack("run/needs encoding"))

    assert result["status"] == "ok"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/runs/run%2Fneeds%20encoding/restart-stack"
    assert captured["kwargs"]["timeout"] == ControllerClient.RUN_CONTROL_TIMEOUT_SECONDS


def test_request_controller_maps_timeout_to_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ControllerClient(_Settings())

    class _TimeoutClient:
        async def request(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ReadTimeout("upstream timeout")

    async def _fake_get_client() -> _TimeoutClient:
        return _TimeoutClient()

    monkeypatch.setattr(client, "_get_client", _fake_get_client)

    with pytest.raises(RuntimeError, match="timed out"):
        _run(client._request_controller("POST", "/api/v1/runs/run-1/stop"))


def test_list_runtimes_uses_controller_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return [{"id": "openclaw"}]

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.list_runtimes())

    assert result == [{"id": "openclaw"}]
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/runtimes"


def test_list_environments_accepts_array_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return [{"id": "moltbook"}]

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.list_environments())

    assert result == [{"id": "moltbook"}]
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/environments"


def test_create_run_posts_to_runs_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {"run_id": "run-1"}

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.create_run({"environment_id": "moltbook"}))

    assert result["run_id"] == "run-1"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/runs"
    assert captured["kwargs"]["payload"] == {"environment_id": "moltbook"}


def test_list_runs_uses_environment_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return [{"run_id": "run-1"}]

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.list_runs(environment_id="moltbook", status="running"))

    assert result == [{"run_id": "run-1"}]
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/runs"
    assert captured["kwargs"]["params"] == {"environment_id": "moltbook", "status": "running"}


def test_get_run_snapshot_uses_snapshot_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {"run_id": "run-1", "snapshot_hash": "sha256:abc"}

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.get_run_snapshot("run-1"))

    assert result["snapshot_hash"] == "sha256:abc"
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/runs/run-1/snapshot"


def test_get_run_condition_manifest_uses_controller_path_and_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ControllerClient(_Settings())
    captured: dict[str, Any] = {}

    async def _fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kwargs
        return {"run_id": "run-1", "source": "native"}

    monkeypatch.setattr(client, "_request_controller", _fake_request)

    result = _run(client.get_run_condition_manifest("run-1", mode="best_effort"))

    assert result["run_id"] == "run-1"
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/runs/run-1/condition-manifest"
    assert captured["kwargs"]["params"] == {"mode": "best_effort"}


def test_get_run_condition_manifest_csv_returns_content_and_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ControllerClient(_Settings())

    class _FakeHttpClient:
        async def get(self, url: str, params: Any = None) -> httpx.Response:
            assert url.endswith("/api/v1/runs/run-1/condition-manifest.csv")
            assert params == {"mode": "strict"}
            return httpx.Response(
                status_code=200,
                text="run_id,source\nrun-1,native\n",
                headers={"content-disposition": 'attachment; filename="run-1-condition-manifest.csv"'},
            )

    async def _fake_get_client() -> _FakeHttpClient:
        return _FakeHttpClient()

    monkeypatch.setattr(client, "_get_client", _fake_get_client)

    result = _run(client.get_run_condition_manifest_csv("run-1", mode="strict"))

    assert result["content"].startswith("run_id,source")
    assert "condition-manifest.csv" in (result["content_disposition"] or "")


def test_admin_route_condition_manifest_csv_wraps_proxy_result() -> None:
    class _Controller:
        def __init__(self) -> None:
            self.closed = False

        async def get_run_condition_manifest_csv(self, run_id: str, mode: str = "strict") -> dict[str, Any]:
            assert run_id == "run-1"
            assert mode == "best_effort"
            return {
                "content": "run_id,source\nrun-1,backfill\n",
                "content_disposition": 'attachment; filename="run-1-condition-manifest.csv"',
            }

        async def close(self) -> None:
            self.closed = True

    controller = _Controller()

    response = _run(
        v1.get_run_condition_manifest_csv(
            "run-1",
            mode="best_effort",
            controller=controller,  # type: ignore[arg-type]
        )
    )

    assert response.media_type == "text/csv"
    assert response.headers["content-disposition"].endswith("condition-manifest.csv\"")
    assert response.body.decode("utf-8").startswith("run_id,source")
    assert controller.closed is True


def test_admin_route_condition_manifest_json_wraps_proxy_result() -> None:
    class _Controller:
        def __init__(self) -> None:
            self.closed = False

        async def get_run_condition_manifest(self, run_id: str, mode: str = "strict") -> dict[str, Any]:
            assert run_id == "run-1"
            assert mode == "best_effort"
            return {"run_id": "run-1", "source": "backfill"}

        async def close(self) -> None:
            self.closed = True

    controller = _Controller()

    payload = _run(
        v1.get_run_condition_manifest(
            "run-1",
            mode="best_effort",
            controller=controller,  # type: ignore[arg-type]
        )
    )

    assert payload["run_id"] == "run-1"
    assert payload["source"] == "backfill"
    assert controller.closed is True


def test_admin_route_condition_manifest_json_preserves_proxy_status() -> None:
    controller = _RaisingController(
        "get_run_condition_manifest",
        ControllerProxyError(409, {"code": "condition_manifest_unavailable"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            v1.get_run_condition_manifest(
                "run-1",
                mode="strict",
                controller=controller,  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"code": "condition_manifest_unavailable"}
    assert controller.closed is True
