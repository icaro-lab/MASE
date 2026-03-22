"""Unit tests for scheduler frequency-mode behavior and contract validation."""

import types
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError


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

from app.routes.scheduler import SchedulerConfig
from app.scheduler import HeartbeatScheduler


@pytest.mark.unit
def test_initialize_fixed_mode_sets_expected_defaults() -> None:
    scheduler = HeartbeatScheduler()
    scheduler.initialize(
        interval="11s",
        frequency_mode="fixed",
        timeout="2s",
    )

    assert scheduler._frequency_mode == "fixed"
    assert scheduler._heartbeat_interval == pytest.approx(11.0)
    assert scheduler._heartbeat_interval_min is None
    assert scheduler._heartbeat_interval_max is None
    assert scheduler._sample_next_tick_interval_seconds() == pytest.approx(11.0)


@pytest.mark.unit
def test_initialize_random_range_mode_samples_within_bounds_and_is_seeded() -> None:
    scheduler_a = HeartbeatScheduler()
    scheduler_a.initialize(
        interval="5s",
        frequency_mode="random_range",
        interval_min="3s",
        interval_max="7s",
        random_seed=42,
        timeout="2s",
    )
    scheduler_b = HeartbeatScheduler()
    scheduler_b.initialize(
        interval="5s",
        frequency_mode="random_range",
        interval_min="3s",
        interval_max="7s",
        random_seed=42,
        timeout="2s",
    )

    samples_a = [scheduler_a._sample_next_tick_interval_seconds() for _ in range(5)]
    samples_b = [scheduler_b._sample_next_tick_interval_seconds() for _ in range(5)]

    assert all(3.0 <= value <= 7.0 for value in samples_a)
    assert samples_a == pytest.approx(samples_b)


@pytest.mark.unit
def test_initialize_random_range_rejects_invalid_bounds() -> None:
    scheduler = HeartbeatScheduler()
    with pytest.raises(ValueError, match="interval_max must be >= interval_min"):
        scheduler.initialize(
            interval="5s",
            frequency_mode="random_range",
            interval_min="7s",
            interval_max="3s",
            timeout="2s",
        )


@pytest.mark.unit
def test_scheduler_config_requires_bounds_for_random_range() -> None:
    with pytest.raises(ValidationError, match="heartbeat_interval_min and heartbeat_interval_max"):
        SchedulerConfig(
            frequency_mode="random_range",
            heartbeat_interval="5s",
        )


@pytest.mark.unit
def test_scheduler_config_normalizes_frequency_mode_case() -> None:
    config = SchedulerConfig(
        frequency_mode=" RANDOM_RANGE ",
        heartbeat_interval="5s",
        heartbeat_interval_min="2s",
        heartbeat_interval_max="4s",
    )

    assert config.frequency_mode == "random_range"
