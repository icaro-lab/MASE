"""Unit tests for telemetry agent-launcher URL resolution."""

from __future__ import annotations

import os
import types

import pytest

os.environ.setdefault("SIM_CTRL_METRICS_PATH", "/tmp/mase-test-metrics")
os.environ.setdefault("SIM_CTRL_STATE_PATH", "/tmp/mase-test-state")
os.environ.setdefault("SIM_CTRL_DATABASE_URL", "sqlite:////tmp/mase-test-run.db")

from app.routes import telemetry


class _FakeQuery:
    def __init__(self, payload):
        self._payload = payload

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._payload


class _FakeDb:
    def __init__(self, run):
        self._run = run

    def query(self, _model):
        return _FakeQuery(self._run)


@pytest.mark.unit
def test_resolve_agent_launcher_url_prefers_descriptor_service_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = types.SimpleNamespace(run_id="run-1")
    db = _FakeDb(run)

    monkeypatch.setattr(
        telemetry,
        "_resolve_environment_id_for_run",
        lambda _run, _db: "moltbook",
    )
    monkeypatch.setattr(
        telemetry.run_launcher,
        "get_service_urls",
        lambda run_id, environment_id: {
            "agent_worker": f"http://{environment_id}-agents-{run_id}:8000"
        },
    )
    monkeypatch.setattr(telemetry.run_launcher, "get_run_status", lambda _run_id: {})

    resolved = telemetry._resolve_agent_launcher_url("run-1", db=db)

    assert resolved == "http://moltbook-agents-run-1:8000"


@pytest.mark.unit
def test_resolve_agent_launcher_url_uses_environment_aware_settings_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = types.SimpleNamespace(run_id="run-2")
    db = _FakeDb(run)

    monkeypatch.setattr(
        telemetry,
        "_resolve_environment_id_for_run",
        lambda _run, _db: "moltbook",
    )

    def _raise_descriptor_error(*_args, **_kwargs):
        raise ValueError("runtime descriptor missing")

    monkeypatch.setattr(telemetry.run_launcher, "get_service_urls", _raise_descriptor_error)
    monkeypatch.setattr(telemetry.run_launcher, "get_run_status", lambda _run_id: {})
    monkeypatch.setattr(
        telemetry.settings.__class__,
        "get_agent_worker_url",
        lambda self, run_id, environment_id=None: f"http://fallback-{environment_id}-{run_id}:8000",
    )

    resolved = telemetry._resolve_agent_launcher_url("run-2", db=db)

    assert resolved == "http://fallback-moltbook-run-2:8000"
