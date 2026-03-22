"""Heartbeat scheduler for autonomous agent management."""

import asyncio
import random
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
from urllib.parse import urlparse

import httpx
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.date import DateTrigger
except Exception:  # pragma: no cover - lightweight fallback for minimal test envs
    class DateTrigger:  # type: ignore[override]
        def __init__(self, run_date=None):
            self.run_date = run_date

    class AsyncIOScheduler:  # type: ignore[override]
        def start(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            return None

        def add_job(self, *args, **kwargs) -> None:
            return None

from .config import settings
from .telemetry_client import telemetry_buffer, TelemetryEvent, ActionCategory, EventSource


def _coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort integer coercion for external payload fields."""
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        return int(float(str(value).strip()))
    except Exception:
        return default


def _iter_successful_action_observations(round_details: Any) -> List[Dict[str, Any]]:
    """Extract successful per-action observations from round details."""
    successful: List[Dict[str, Any]] = []
    if not isinstance(round_details, list):
        return successful

    for round_item in round_details:
        if not isinstance(round_item, dict):
            continue
        observations = round_item.get("observations")
        if not isinstance(observations, list):
            continue
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            if bool(obs.get("success")):
                successful.append(obs)
    return successful


class SchedulerState(Enum):
    """Scheduler state machine."""
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class HeartbeatResult:
    """Result of a heartbeat operation."""
    agent_id: str
    success: bool
    actions_executed: int = 0
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentState:
    """State of a single agent."""
    agent_id: str
    run_id: str
    heartbeat_index: int = 0
    status: str = "unknown"
    last_heartbeat: Optional[datetime] = None
    consecutive_failures: int = 0
    total_actions: int = 0
    last_error: Optional[str] = None


class HeartbeatScheduler:
    """Self-contained heartbeat scheduler that manages agents autonomously.
    
    This scheduler:
    - Reads agent list from filesystem (/agents/ folder)
    - Tracks its own state independently
    - Calls the local /heartbeat endpoint for each agent
    - Maintains local agent state tracking
    """
    
    def __init__(self):
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._state = SchedulerState.STOPPED
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._tasks: set = set()
        self._agent_loop_tasks: Dict[str, asyncio.Task] = {}
        self._sync_task: Optional[asyncio.Task] = None
        self._in_flight_heartbeats: int = 0
        self._last_pause_cancelled_tasks: int = 0
        self._agent_states: Dict[str, AgentState] = {}
        self._tick_count: int = 0
        self._start_time: Optional[datetime] = None
        
        # Configuration
        self._heartbeat_interval: float = 300  # 5 minutes default
        self._frequency_mode: str = "fixed"
        self._heartbeat_interval_min: Optional[float] = None
        self._heartbeat_interval_max: Optional[float] = None
        self._random_seed: Optional[int] = None
        self._rng = random.Random()
        self._last_sampled_tick_interval_seconds: Optional[float] = None
        self._heartbeat_timeout: float = 120   # 2 minutes default
        self._max_parallel_agents: int = 10
        self._retry_count: int = 3
        self._retry_delay: float = 10
        self._jitter: float = 5
        self._max_heartbeats_per_agent: Optional[int] = None
        self._model: str = "openai/gpt-5-mini"  # Default model
        self._environment_url: Optional[str] = None
        self._environment_name: Optional[str] = None
        self._allowed_environment_urls: List[str] = []
        self._agent_tokens: Dict[str, str] = {}
        self._agent_models: Dict[str, str] = {}
        self._agent_environment_auth_registry: Dict[str, Dict[str, str]] = {}
        
    def initialize(self, 
                   interval: str = "5m",
                   frequency_mode: str = "fixed",
                   interval_min: Optional[str] = None,
                   interval_max: Optional[str] = None,
                   random_seed: Optional[int] = None,
                   timeout: str = "2m", 
                   max_parallel: int = 10,
                   retry_count: int = 3,
                   retry_delay: str = "10s",
                   jitter: str = "5s",
                   max_heartbeats_per_agent: Optional[int] = None,
                   model: str = "openai/gpt-5-mini",
                   environment_url: Optional[str] = None,
                   environment_name: Optional[str] = None,
                   allowed_environment_urls: Optional[List[str]] = None,
                   agent_tokens: Optional[Dict[str, str]] = None,
                   agent_models: Optional[Dict[str, str]] = None):
        """Initialize scheduler with configuration.
        
        Args:
            interval: Heartbeat interval (e.g., "5m", "30s", "1h")
            frequency_mode: Tick cadence mode (`fixed` or `random_range`)
            interval_min: Minimum interval when frequency_mode is `random_range`
            interval_max: Maximum interval when frequency_mode is `random_range`
            random_seed: Optional deterministic seed for random interval sampling
            timeout: Heartbeat timeout (e.g., "2m", "30s")
            max_parallel: Maximum concurrent heartbeat operations
            retry_count: Number of retries on failure
            retry_delay: Delay between retries
            jitter: Random delay spread to avoid thundering herd
            model: LLM model to use for agent heartbeats
            environment_url: Environment URL passed to heartbeat endpoint
            environment_name: Environment name for runtime context
            allowed_environment_urls: Optional additional allowlisted environment URLs
            agent_tokens: Per-agent auth token map (keyed by agent_id)
        """
        normalized_mode = str(frequency_mode or "fixed").strip().lower()
        if normalized_mode not in {"fixed", "random_range"}:
            raise ValueError(
                f"Invalid frequency_mode='{frequency_mode}'. Expected 'fixed' or 'random_range'."
            )

        base_interval = self._parse_interval(interval)
        self._frequency_mode = normalized_mode
        if normalized_mode == "random_range":
            min_interval = self._parse_interval(interval_min or interval)
            max_interval = self._parse_interval(interval_max or interval)
            if min_interval <= 0:
                raise ValueError("interval_min must be > 0 for random_range mode")
            if max_interval < min_interval:
                raise ValueError("interval_max must be >= interval_min for random_range mode")
            self._heartbeat_interval = min_interval
            self._heartbeat_interval_min = min_interval
            self._heartbeat_interval_max = max_interval
        else:
            self._heartbeat_interval = base_interval
            self._heartbeat_interval_min = None
            self._heartbeat_interval_max = None

        self._random_seed = int(random_seed) if random_seed is not None else None
        self._rng = random.Random(self._random_seed)
        self._last_sampled_tick_interval_seconds = None
        self._heartbeat_timeout = self._parse_interval(timeout)
        self._max_parallel_agents = max_parallel
        self._retry_count = retry_count
        self._retry_delay = self._parse_interval(retry_delay)
        self._jitter = self._parse_interval(jitter)
        self._max_heartbeats_per_agent = (
            int(max_heartbeats_per_agent)
            if max_heartbeats_per_agent is not None and int(max_heartbeats_per_agent) > 0
            else None
        )
        self._model = model
        self._environment_url = environment_url
        self._environment_name = environment_name
        self._allowed_environment_urls = [
            str(url).strip()
            for url in (allowed_environment_urls or [])
            if str(url).strip()
        ]
        self._agent_tokens = {
            str(key): str(value)
            for key, value in (agent_tokens or {}).items()
        }
        self._agent_models = {
            str(key): str(value).strip()
            for key, value in (agent_models or {}).items()
            if str(value or "").strip()
        }
        self._agent_environment_auth_registry = {}
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._agent_loop_tasks = {}
        self._sync_task = None
        self._in_flight_heartbeats = 0
        # Runtime reconfiguration must reset state for deterministic tick/bootstrap behavior.
        self._tick_count = 0
        self._agent_states.clear()
        self._tasks = set()
        
    async def start(self):
        """Start the heartbeat scheduler."""
        if self._state == SchedulerState.RUNNING:
            return
        
        # Start telemetry buffer
        await telemetry_buffer.start()
        
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()
        self._state = SchedulerState.RUNNING
        self._start_time = datetime.utcnow()
        self._last_sampled_tick_interval_seconds = 0.0
        await self._sync_agents_once(initial_bootstrap=True)
        self._sync_task = asyncio.create_task(self._sync_agents_loop())

        print(
            "Heartbeat scheduler started "
            f"(mode={self._frequency_mode}, interval={self._heartbeat_interval}s, per-agent loops active)"
        )
    
    async def stop(self):
        """Stop the heartbeat scheduler."""
        if self._state == SchedulerState.STOPPED:
            return
        
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

        if self._sync_task:
            self._sync_task.cancel()
            self._sync_task = None

        for task in self._agent_loop_tasks.values():
            task.cancel()
        self._agent_loop_tasks.clear()

        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        
        # Stop telemetry buffer (flushes remaining events)
        await telemetry_buffer.stop()
        
        self._state = SchedulerState.STOPPED
        self._start_time = None
        print("Heartbeat scheduler stopped")
    
    def pause(self):
        """Pause the scheduler."""
        if self._state == SchedulerState.RUNNING:
            self._state = SchedulerState.PAUSED
            cancelled = 0
            if self._sync_task and not self._sync_task.done():
                self._sync_task.cancel()
                cancelled += 1
            self._sync_task = None
            for task in list(self._agent_loop_tasks.values()):
                if task.done():
                    continue
                task.cancel()
                cancelled += 1
            self._agent_loop_tasks.clear()
            for task in list(self._tasks):
                if task.done():
                    continue
                task.cancel()
                cancelled += 1
            self._last_pause_cancelled_tasks = cancelled
            print(f"Heartbeat scheduler paused (cancelled_tasks={cancelled})")

        return self._last_pause_cancelled_tasks
    
    def resume(self):
        """Resume the scheduler."""
        if self._state == SchedulerState.PAUSED:
            self._state = SchedulerState.RUNNING
            self._last_pause_cancelled_tasks = 0
            asyncio.create_task(self._sync_agents_once(initial_bootstrap=False))
            self._sync_task = asyncio.create_task(self._sync_agents_loop())
            print("Heartbeat scheduler resumed")

    def is_paused(self) -> bool:
        return self._state == SchedulerState.PAUSED
    
    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status."""
        active_agents = sum(1 for s in self._agent_states.values() 
                          if s.status in ("active", "running"))
        failed_agents = sum(1 for s in self._agent_states.values() 
                          if s.consecutive_failures > 0)
        
        return {
            "state": self._state.value,
            "tick_count": self._tick_count,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "uptime_seconds": (datetime.utcnow() - self._start_time).total_seconds() 
                             if self._start_time else 0,
            "config": {
                "heartbeat_interval": self._heartbeat_interval,
                "frequency_mode": self._frequency_mode,
                "heartbeat_interval_min": self._heartbeat_interval_min,
                "heartbeat_interval_max": self._heartbeat_interval_max,
                "random_seed": self._random_seed,
                "last_sampled_tick_interval_ms": int(self._last_sampled_tick_interval_seconds * 1000)
                if self._last_sampled_tick_interval_seconds is not None
                else None,
                "heartbeat_timeout": self._heartbeat_timeout,
                "max_parallel_agents": self._max_parallel_agents,
                "max_heartbeats_per_agent": self._max_heartbeats_per_agent,
                "environment_url": self._environment_url,
                "environment_name": self._environment_name,
                "allowed_environment_urls_count": len(self._allowed_environment_urls),
                "agent_tokens_count": len(self._agent_tokens),
                "agent_models_count": len(self._agent_models),
                "agent_auth_registry_entries": sum(
                    len(entries)
                    for entries in self._agent_environment_auth_registry.values()
                ),
                "in_flight_tasks": self._in_flight_heartbeats,
                "last_pause_cancelled_tasks": self._last_pause_cancelled_tasks,
            },
            "agents": {
                "total": len(self._agent_states),
                "active": active_agents,
                "failed": failed_agents,
            },
            "agent_states": [
                {
                    "agent_id": s.agent_id,
                    "run_id": s.run_id,
                    "heartbeat_index": s.heartbeat_index,
                    "status": s.status,
                    "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else None,
                    "consecutive_failures": s.consecutive_failures,
                    "total_actions": s.total_actions,
                }
                for s in self._agent_states.values()
            ]
        }

    def get_progress(self) -> Dict[str, Any]:
        """Get lightweight aggregate scheduler progress for coordination paths."""
        states = list(self._agent_states.values())
        heartbeat_indexes = [int(s.heartbeat_index) for s in states]
        active_agents = sum(1 for s in states if s.status in ("active", "running"))
        completed_agents = sum(1 for s in states if s.status == "completed")
        failed_agents = sum(1 for s in states if s.consecutive_failures > 0 or s.status == "failed")
        all_agents_terminal = bool(states) and all(
            s.status in {"completed", "failed"} for s in states
        )

        return {
            "state": self._state.value,
            "tick_count": self._tick_count,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "uptime_seconds": (datetime.utcnow() - self._start_time).total_seconds()
            if self._start_time
            else 0,
            "config": {
                "heartbeat_interval": self._heartbeat_interval,
                "frequency_mode": self._frequency_mode,
                "heartbeat_timeout": self._heartbeat_timeout,
                "max_parallel_agents": self._max_parallel_agents,
                "max_heartbeats_per_agent": self._max_heartbeats_per_agent,
                "in_flight_tasks": self._in_flight_heartbeats,
            },
            "agents": {
                "total": len(states),
                "active": active_agents,
                "completed": completed_agents,
                "failed": failed_agents,
            },
            "progress": {
                "min_heartbeat_index": min(heartbeat_indexes) if heartbeat_indexes else 0,
                "max_heartbeat_index": max(heartbeat_indexes) if heartbeat_indexes else 0,
                "completed_agents": completed_agents,
                "active_agents": active_agents,
                "failed_agents": failed_agents,
                "total_agents": len(states),
                "all_agents_terminal": all_agents_terminal,
            },
        }

    def get_agent_states_page(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get paginated detailed agent state for drilldown/debug paths."""
        normalized_limit = max(1, min(int(limit), 500))
        normalized_offset = max(0, int(offset))
        normalized_status = str(status_filter or "").strip().lower() or None

        all_states = [
            {
                "agent_id": s.agent_id,
                "run_id": s.run_id,
                "heartbeat_index": s.heartbeat_index,
                "status": s.status,
                "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else None,
                "consecutive_failures": s.consecutive_failures,
                "total_actions": s.total_actions,
            }
            for s in self._agent_states.values()
        ]
        all_states.sort(key=lambda item: (str(item["run_id"]), str(item["agent_id"])))
        if normalized_status:
            all_states = [
                item
                for item in all_states
                if str(item.get("status") or "").strip().lower() == normalized_status
            ]

        total = len(all_states)
        page = all_states[normalized_offset : normalized_offset + normalized_limit]
        return {
            "total_agents": len(self._agent_states),
            "filtered_total": total,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "status_filter": normalized_status,
            "agents": page,
        }

    def _all_agents_terminal(self) -> bool:
        """Return True when all known agents are terminal."""
        if not self._agent_states:
            return False
        return all(state.status in {"completed", "failed"} for state in self._agent_states.values())

    def _maybe_stop_when_all_agents_terminal(self) -> None:
        """Stop the local scheduler once all agents have reached a terminal state."""
        if self._state != SchedulerState.RUNNING:
            return
        if self._in_flight_heartbeats > 0:
            return
        if any(not task.done() for task in self._agent_loop_tasks.values()):
            return
        if not self._all_agents_terminal():
            return
        self._state = SchedulerState.STOPPED
    
    async def _run_heartbeats(self):
        """Compatibility shim: sync per-agent loops with current filesystem state."""
        await self._sync_agents_once(initial_bootstrap=(self._tick_count == 0))

    async def _sync_agents_loop(self) -> None:
        """Continuously reconcile per-agent loops with filesystem-discovered agents."""
        try:
            while self._state == SchedulerState.RUNNING:
                await self._sync_agents_once(initial_bootstrap=False)
                await asyncio.sleep(max(min(self._heartbeat_interval / 2.0, 5.0), 1.0))
        except asyncio.CancelledError:
            return

    async def _sync_agents_once(self, *, initial_bootstrap: bool) -> None:
        """Create/cancel per-agent loops based on current filesystem state."""
        if self._state != SchedulerState.RUNNING:
            return

        agents = self._discover_agents()
        discovered: Dict[str, tuple[str, str]] = {}
        for agent_id, run_id in agents:
            state_key = f"{run_id}/{agent_id}"
            discovered[state_key] = (agent_id, run_id)
            if state_key not in self._agent_states:
                self._agent_states[state_key] = AgentState(
                    agent_id=agent_id,
                    run_id=run_id,
                )

            agent_state = self._agent_states[state_key]
            if self._agent_reached_heartbeat_cap(agent_state):
                agent_state.status = "completed"
                existing_task = self._agent_loop_tasks.get(state_key)
                if existing_task and existing_task.done():
                    self._agent_loop_tasks.pop(state_key, None)
                continue

            existing_task = self._agent_loop_tasks.get(state_key)
            if existing_task and existing_task.done():
                self._agent_loop_tasks.pop(state_key, None)
                existing_task = None

            if existing_task is None:
                task = asyncio.create_task(
                    self._agent_loop(
                        state_key=state_key,
                        agent_id=agent_id,
                        run_id=run_id,
                        initial_delay=self._sample_initial_agent_delay(initial_bootstrap),
                    )
                )
                self._agent_loop_tasks[state_key] = task

        for state_key, task in list(self._agent_loop_tasks.items()):
            if state_key in discovered:
                continue
            if not task.done():
                task.cancel()
            self._agent_loop_tasks.pop(state_key, None)
            self._agent_states.pop(state_key, None)

        self._maybe_stop_when_all_agents_terminal()

    def _sample_initial_agent_delay(self, initial_bootstrap: bool) -> float:
        """Sample per-agent bootstrap delay to avoid synchronized first actions."""
        if not initial_bootstrap:
            return 0.0
        upper_bound = min(
            max(float(self._jitter), 0.0),
            max(float(self._heartbeat_interval), 0.0),
        )
        if upper_bound <= 0:
            return 0.0
        return self._rng.uniform(0.0, upper_bound)

    async def _agent_loop(
        self,
        *,
        state_key: str,
        agent_id: str,
        run_id: str,
        initial_delay: float,
    ) -> None:
        """Run an independent heartbeat loop for a single agent."""
        try:
            if initial_delay > 0:
                await asyncio.sleep(initial_delay)

            while self._state == SchedulerState.RUNNING:
                agent_state = self._agent_states.get(state_key)
                if agent_state and self._agent_reached_heartbeat_cap(agent_state):
                    agent_state.status = "completed"
                    break

                result = await self._run_single_heartbeat(
                    agent_id=agent_id,
                    run_id=run_id,
                    jitter_delay=0.0,
                )
                self._tick_count += 1
                self._update_agent_state(result)

                if self._state != SchedulerState.RUNNING:
                    break

                agent_state = self._agent_states.get(state_key)
                if agent_state and self._agent_reached_heartbeat_cap(agent_state):
                    agent_state.status = "completed"
                    break

                await asyncio.sleep(self._sample_next_tick_interval_seconds())
        except asyncio.CancelledError:
            return
        finally:
            current = self._agent_loop_tasks.get(state_key)
            if current is asyncio.current_task():
                self._agent_loop_tasks.pop(state_key, None)
            self._maybe_stop_when_all_agents_terminal()
    
    async def _run_single_heartbeat(
        self,
        agent_id: str,
        run_id: str,
        jitter_delay: float
    ) -> HeartbeatResult:
        """Run heartbeat for a single agent."""
        try:
            # Apply jitter
            if jitter_delay > 0:
                await asyncio.sleep(jitter_delay)

            # Scheduler could be paused/stopped while this task was queued.
            if self._state != SchedulerState.RUNNING:
                return HeartbeatResult(
                    agent_id=agent_id,
                    success=True,
                    actions_executed=0,
                    metadata={"skipped": "scheduler_not_running"},
                )
            
            # Get or create agent state
            state_key = f"{run_id}/{agent_id}"
            if state_key not in self._agent_states:
                self._agent_states[state_key] = AgentState(
                    agent_id=agent_id,
                    run_id=run_id
                )
            
            agent_state = self._agent_states[state_key]
            agent_state.status = "running"
            next_heartbeat_index = int(agent_state.heartbeat_index) + 1
            
            # Call heartbeat with retry logic inside the actual concurrency gate.
            result = None
            async with self._semaphore:
                if self._state != SchedulerState.RUNNING:
                    return HeartbeatResult(
                        agent_id=agent_id,
                        success=True,
                        actions_executed=0,
                        metadata={"skipped": "scheduler_not_running"},
                    )

                self._in_flight_heartbeats += 1
                try:
                    for attempt in range(self._retry_count):
                        if self._state != SchedulerState.RUNNING:
                            result = HeartbeatResult(
                                agent_id=agent_id,
                                success=True,
                                actions_executed=0,
                                metadata={"skipped": "scheduler_not_running"},
                            )
                            break
                        try:
                            result = await self._call_heartbeat_endpoint(
                                agent_id=agent_id,
                                run_id=run_id,
                                environment_url=self._environment_url,
                                heartbeat_index=next_heartbeat_index,
                            )
                            agent_state.heartbeat_index = next_heartbeat_index
                            break

                        except asyncio.CancelledError:
                            result = HeartbeatResult(
                                agent_id=agent_id,
                                success=True,
                                actions_executed=0,
                                metadata={"skipped": "cancelled"},
                            )
                            break

                        except Exception as e:
                            error_msg = str(e)
                            print(f"[_run_single_heartbeat] Attempt {attempt + 1}/{self._retry_count} "
                                  f"failed for {agent_id}: {error_msg}")

                            if attempt < self._retry_count - 1:
                                await asyncio.sleep(self._retry_delay)
                            else:
                                # All retries exhausted
                                agent_state.consecutive_failures += 1
                                agent_state.last_error = error_msg
                                agent_state.status = "failed"

                                result = HeartbeatResult(
                                    agent_id=agent_id,
                                    success=False,
                                    error=error_msg
                                )
                finally:
                    self._in_flight_heartbeats = max(0, self._in_flight_heartbeats - 1)
            
            # Emit run-scoped heartbeat telemetry (non-blocking)
            if result:
                await self._report_heartbeat_to_controller(
                    agent_id=agent_id,
                    run_id=run_id,
                    result=result
                )
            
            return result
        except asyncio.CancelledError:
            return HeartbeatResult(
                agent_id=agent_id,
                success=True,
                actions_executed=0,
                metadata={"skipped": "cancelled"},
            )

    async def _call_heartbeat_endpoint(
        self,
        agent_id: str,
        run_id: str,
        environment_url: Optional[str] = None,
        heartbeat_index: Optional[int] = None,
    ) -> HeartbeatResult:
        """Call the local /heartbeat endpoint."""
        # Determine base URL - use localhost since we're calling ourselves
        base_url = f"http://localhost:{settings.service_port or 8000}"
        resolved_model = self._resolve_agent_model(agent_id, run_id)
        payload = {
            "agent_id": agent_id,
            "run_id": run_id,
            "model": resolved_model,
        }
        resolved_agent_token = self._resolve_agent_token(agent_id, run_id)
        if resolved_agent_token:
            payload["agent_token"] = resolved_agent_token
        if environment_url:
            payload["environment_url"] = environment_url
        if self._environment_name:
            payload["environment_name"] = self._environment_name
        if self._allowed_environment_urls:
            payload["allowed_environment_urls"] = list(self._allowed_environment_urls)
        payload["tick"] = self._tick_count
        if heartbeat_index is not None:
            payload["heartbeat_index"] = int(heartbeat_index)

        async with httpx.AsyncClient(timeout=self._heartbeat_timeout) as client:
            response = await client.post(
                f"{base_url}/heartbeat",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            heartbeat_status = str(data.get("status") or "")
            round_details = data.get("round_details") or []
            if not isinstance(round_details, list):
                round_details = []

            policy_source = data.get("policy_source")
            prompt_contract_version = data.get("prompt_contract_version")
            interaction_mode = data.get("interaction_mode")
            memory_mode = data.get("memory_mode")

            if isinstance(round_details, list):
                for item in round_details:
                    if not isinstance(item, dict):
                        continue
                    if not policy_source and item.get("policy_source"):
                        policy_source = item.get("policy_source")
                    if not prompt_contract_version and item.get("prompt_contract_version"):
                        prompt_contract_version = item.get("prompt_contract_version")
                    if not interaction_mode and item.get("interaction_mode"):
                        interaction_mode = item.get("interaction_mode")
                    if not memory_mode and item.get("memory_mode"):
                        memory_mode = item.get("memory_mode")

            memory_turns_loaded = data.get("memory_turns_loaded")
            if memory_turns_loaded is None and isinstance(round_details, list):
                memory_turns_loaded = max(
                    [
                        _coerce_int(item.get("memory_turns_loaded"), 0)
                        for item in round_details
                        if isinstance(item, dict)
                    ]
                    or [0]
                )

            return HeartbeatResult(
                agent_id=agent_id,
                # Paused/skipped are intentional control states, not failures.
                success=heartbeat_status in {"completed", "paused", "skipped"},
                actions_executed=data.get("actions_executed", 0),
                error=data.get("error", {}).get("message") if data.get("error") else None,
                metadata={
                    "llm_cost": float(data.get("llm_cost", 0.0) or 0.0),
                    "model": resolved_model,
                    "heartbeat_status": heartbeat_status,
                    "stop_reason": data.get("stop_reason"),
                    "rounds_executed": _coerce_int(data.get("rounds_executed"), 0),
                    "total_model_calls": _coerce_int(data.get("model_calls"), 0),
                    "elapsed_ms": _coerce_int(data.get("elapsed_ms"), 0),
                    "policy_source": policy_source,
                    "prompt_contract_version": prompt_contract_version,
                    "interaction_mode": interaction_mode,
                    "memory_mode": memory_mode,
                    "memory_turns_loaded": _coerce_int(memory_turns_loaded, 0),
                    "heartbeat_index": _coerce_int(data.get("heartbeat_index"), heartbeat_index or 0),
                    "round_details": round_details,
                },
            )

    def _resolve_agent_token(self, agent_id: str, run_id: str) -> Optional[str]:
        """Resolve token by common key formats."""
        if agent_id in self._agent_tokens:
            return self._agent_tokens[agent_id]

        key_candidates = (
            f"{run_id}/{agent_id}",
            f"{run_id}:{agent_id}",
        )
        for key in key_candidates:
            if key in self._agent_tokens:
                return self._agent_tokens[key]

        return None

    def _resolve_agent_model(self, agent_id: str, run_id: str) -> str:
        """Resolve model by common key formats, fallback to scheduler default."""
        if agent_id in self._agent_models and str(self._agent_models[agent_id]).strip():
            return str(self._agent_models[agent_id]).strip()

        key_candidates = (
            f"{run_id}/{agent_id}",
            f"{run_id}:{agent_id}",
        )
        for key in key_candidates:
            if key in self._agent_models and str(self._agent_models[key]).strip():
                return str(self._agent_models[key]).strip()

        return self._model
    
    async def _report_heartbeat_to_controller(
        self,
        agent_id: str,
        run_id: str,
        result: HeartbeatResult
    ) -> None:
        """Emit heartbeat outcome telemetry for controller ingestion.
        
        Args:
            agent_id: Agent identifier
            run_id: Run identifier
            result: Heartbeat execution result
        """
        # Emit telemetry events for heartbeat execution
        try:
            # Record heartbeat execution event
            round_details = (result.metadata or {}).get("round_details") or []
            gate_details = None
            if isinstance(round_details, list):
                for item in round_details:
                    if isinstance(item, dict) and isinstance(item.get("gate"), dict):
                        gate_details = item.get("gate")
                        break

            event = TelemetryEvent(
                run_id=run_id,
                agent_id=agent_id,
                action_type="heartbeat",
                action_category=ActionCategory.SYSTEM.value,
                source=EventSource.AGENT.value,
                success=result.success,
                payload={
                    "event_type": "heartbeat_result",
                    "tick": self._tick_count,
                    "heartbeat_index": _coerce_int((result.metadata or {}).get("heartbeat_index"), 0),
                    "action_type": "heartbeat",
                    "action_name": "heartbeat",
                    "actions_executed": result.actions_executed,
                    "heartbeat_status": (result.metadata or {}).get("heartbeat_status"),
                    "stop_reason": (result.metadata or {}).get("stop_reason"),
                    "rounds_executed": _coerce_int((result.metadata or {}).get("rounds_executed"), 0),
                    "total_model_calls": _coerce_int((result.metadata or {}).get("total_model_calls"), 0),
                    "elapsed_ms": _coerce_int((result.metadata or {}).get("elapsed_ms"), 0),
                    "sampled_interval_ms": int(self._last_sampled_tick_interval_seconds * 1000)
                    if self._last_sampled_tick_interval_seconds is not None
                    else None,
                    "frequency_mode": self._frequency_mode,
                    "interval_min_ms": int(self._heartbeat_interval_min * 1000)
                    if self._heartbeat_interval_min is not None
                    else None,
                    "interval_max_ms": int(self._heartbeat_interval_max * 1000)
                    if self._heartbeat_interval_max is not None
                    else None,
                    "policy_source": (result.metadata or {}).get("policy_source"),
                    "prompt_contract_version": (result.metadata or {}).get("prompt_contract_version"),
                    "interaction_mode": (result.metadata or {}).get("interaction_mode"),
                    "memory_mode": (result.metadata or {}).get("memory_mode"),
                    "memory_turns_loaded": _coerce_int((result.metadata or {}).get("memory_turns_loaded"), 0),
                    "gate": gate_details,
                    "status_code": 200 if result.success else None,
                    "error": result.error,
                    "error_code": "heartbeat_failed" if not result.success else None,
                },
                error_message=result.error
            )
            await telemetry_buffer.add_event(event)
            
            # Record action_success only for concrete successful action observations.
            successful_observations = _iter_successful_action_observations(
                (result.metadata or {}).get("round_details")
            )
            if result.success and successful_observations:
                for i, obs in enumerate(successful_observations):
                    action_type = str(obs.get("action_type") or "action_executed")
                    action_name = str(obs.get("action_name") or action_type)
                    payload = {
                        "event_type": "action_success",
                        "tick": self._tick_count,
                        "heartbeat_index": _coerce_int((result.metadata or {}).get("heartbeat_index"), 0),
                        "action_type": action_type,
                        "action_name": action_name,
                        "status_code": _coerce_int(obs.get("status_code"), 200),
                        "error_code": None,
                        "action_index": i,
                        "heartbeat_id": result.timestamp.isoformat() if result.timestamp else None,
                    }
                    if obs.get("action_key") is not None:
                        payload["action_key"] = str(obs.get("action_key"))
                    if obs.get("method") is not None:
                        payload["method"] = str(obs.get("method"))
                    if obs.get("operation") is not None:
                        payload["operation"] = str(obs.get("operation"))
                    if obs.get("path") is not None:
                        payload["path"] = str(obs.get("path"))
                    elif obs.get("url") is not None:
                        payload["path"] = str(obs.get("url"))

                    action_event = TelemetryEvent(
                        run_id=run_id,
                        agent_id=agent_id,
                        action_type=action_type,
                        action_category=ActionCategory.SELF.value,
                        source=EventSource.AGENT.value,
                        success=True,
                        payload=payload,
                    )
                    await telemetry_buffer.add_event(action_event)
                    
        except Exception as e:
            # Don't fail the heartbeat if telemetry fails
            print(
                "[_report_heartbeat_to_controller] Error emitting telemetry "
                f"run_id={run_id} agent_id={agent_id} "
                f"error_code=telemetry_emit_failed error_detail={e}"
            )

    def _sample_next_tick_interval_seconds(self) -> float:
        """Sample next tick delay according to the configured frequency mode."""
        if (
            self._frequency_mode == "random_range"
            and self._heartbeat_interval_min is not None
            and self._heartbeat_interval_max is not None
        ):
            sampled = self._rng.uniform(self._heartbeat_interval_min, self._heartbeat_interval_max)
        else:
            sampled = self._heartbeat_interval

        if sampled <= 0:
            sampled = max(self._heartbeat_interval, 0.1)
        self._last_sampled_tick_interval_seconds = float(sampled)
        return float(sampled)

    def _agent_reached_heartbeat_cap(self, agent_state: AgentState) -> bool:
        """Return True when the agent reached the configured heartbeat budget."""
        cap = self._max_heartbeats_per_agent
        if cap is None or cap <= 0:
            return False
        return int(agent_state.heartbeat_index) >= int(cap)

    def _schedule_next_tick(self, delay_seconds: float) -> None:
        """Schedule a one-shot heartbeat cycle."""
        if not self._scheduler:
            return

        run_at = datetime.utcnow() + timedelta(seconds=max(float(delay_seconds), 0.0))
        self._scheduler.add_job(
            self._run_heartbeats,
            trigger=DateTrigger(run_date=run_at),
            id="heartbeat_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    
    def _discover_agents(self) -> List[tuple]:
        """Discover agents from filesystem.
        
        Returns:
            List of (agent_id, run_id) tuples
        """
        agents = []
        base_path = Path(settings.agents_base_path)
        
        if not base_path.exists():
            return agents
        
        # Iterate through agent directories directly in /agents/ folder.
        for agent_dir in base_path.iterdir():
            if not agent_dir.is_dir():
                continue
            
            agent_id = agent_dir.name
            
            run_id_file = agent_dir / ".run_id"
            if run_id_file.exists():
                try:
                    with open(run_id_file, 'r') as f:
                        run_id = f.read().strip()
                except Exception:
                    run_id = settings.run_id or agent_id
            else:
                run_id = settings.run_id or agent_id
            
            # Check if it looks like an agent directory.
            workspace_dir = agent_dir / "workspace"
            marker_files = (
                workspace_dir / "AGENTS.md",
                workspace_dir / "HEARTBEAT.md",
                workspace_dir / "TOOLS.md",
                workspace_dir / "IDENTITY.md",
            )
            if any(path.exists() for path in marker_files):
                agents.append((agent_id, run_id))
        
        return agents
    
    def _update_agent_state(self, result: HeartbeatResult):
        """Update agent state based on heartbeat result."""
        # Find agent state by agent_id
        for state in self._agent_states.values():
            if state.agent_id == result.agent_id:
                if result.success:
                    state.status = "active"
                    state.consecutive_failures = 0
                    state.total_actions += result.actions_executed
                else:
                    state.consecutive_failures += 1
                    state.last_error = result.error
                    if state.consecutive_failures >= 3:
                        state.status = "failed"
                
                state.last_heartbeat = result.timestamp
                break
        self._maybe_stop_when_all_agents_terminal()
    
    def _parse_interval(self, interval_str: str) -> float:
        """Parse interval string to seconds.
        
        Supports: 5m (minutes), 2h (hours), 30s (seconds)
        """
        interval_str = str(interval_str).strip().lower()

        if not interval_str:
            return 0.0
        
        if interval_str.endswith('m'):
            return float(interval_str[:-1]) * 60
        elif interval_str.endswith('h'):
            return float(interval_str[:-1]) * 3600
        elif interval_str.endswith('s'):
            return float(interval_str[:-1])
        else:
            return float(interval_str)


# Global scheduler instance
heartbeat_scheduler = HeartbeatScheduler()
