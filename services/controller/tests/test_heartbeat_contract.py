"""Unit tests for scheduler timeout alignment with heartbeat runtime budgets."""

import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "services" / "controller" / "app" / "heartbeat_contract.py"
MODULE_SPEC = importlib.util.spec_from_file_location("heartbeat_contract", MODULE_PATH)
assert MODULE_SPEC is not None
heartbeat_contract = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(heartbeat_contract)
resolve_scheduler_heartbeat_timeout = heartbeat_contract.resolve_scheduler_heartbeat_timeout


@pytest.mark.unit
def test_scheduler_timeout_default_aligns_to_policy_runtime_default() -> None:
    timeout = resolve_scheduler_heartbeat_timeout({})
    assert timeout == "120s"


@pytest.mark.unit
def test_scheduler_timeout_keeps_larger_configured_timeout() -> None:
    timeout = resolve_scheduler_heartbeat_timeout(
        {"timeout": "200s", "max_tick_runtime_ms": 90000}
    )
    assert timeout == "200s"


@pytest.mark.unit
def test_scheduler_timeout_raises_when_tick_runtime_needs_more_budget() -> None:
    timeout = resolve_scheduler_heartbeat_timeout(
        {"timeout": "120s", "max_tick_runtime_ms": 200000}
    )
    assert timeout == "230s"


@pytest.mark.unit
def test_scheduler_timeout_handles_malformed_max_tick_runtime_ms() -> None:
    timeout = resolve_scheduler_heartbeat_timeout(
        {"timeout": "30s", "max_tick_runtime_ms": "not-a-number"}
    )
    assert timeout == "120s"


@pytest.mark.unit
def test_scheduler_timeout_handles_non_positive_max_tick_runtime_ms() -> None:
    timeout = resolve_scheduler_heartbeat_timeout(
        {"timeout": "20s", "max_tick_runtime_ms": 0}
    )
    assert timeout == "120s"


@pytest.mark.integration
def test_scheduler_timeout_contract_integration_smoke() -> None:
    """Integration marker smoke for runtime-policy timeout contract."""
    timeout = resolve_scheduler_heartbeat_timeout(
        {"timeout": "90s", "max_tick_runtime_ms": 90000}
    )
    assert timeout == "120s"
