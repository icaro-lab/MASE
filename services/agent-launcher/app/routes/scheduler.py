"""API routes for heartbeat scheduler control."""

import json
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from typing import Optional, Dict, Any

from ..config import settings
from ..scheduler import heartbeat_scheduler, SchedulerState


router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class SchedulerConfig(BaseModel):
    """Configuration for the heartbeat scheduler."""
    frequency_mode: str = Field(
        default="fixed",
        description="Tick cadence mode: fixed | random_range",
    )
    heartbeat_interval: str = Field(default="5m", description="Heartbeat interval (e.g., 5m, 30s, 1h)")
    heartbeat_interval_min: Optional[str] = Field(
        default=None,
        description="Minimum interval for random_range mode",
    )
    heartbeat_interval_max: Optional[str] = Field(
        default=None,
        description="Maximum interval for random_range mode",
    )
    random_seed: Optional[int] = Field(
        default=None,
        description="Optional deterministic seed for random_range sampling",
    )
    heartbeat_timeout: str = Field(default="2m", description="Heartbeat timeout (e.g., 2m, 30s)")
    max_parallel_agents: int = Field(
        default=10,
        ge=1,
        le=5000,
        description="Maximum concurrent heartbeat operations",
    )
    retry_count: int = Field(default=3, ge=1, le=10, description="Number of retries per heartbeat call")
    retry_delay: str = Field(default="10s", description="Delay between retries")
    jitter: str = Field(default="5s", description="Per-agent jitter to spread heartbeats")
    max_heartbeats_per_agent: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional per-agent heartbeat budget; capped agents stop looping locally",
    )
    model: str = Field(default="openai/gpt-5-mini", description="LLM model to use for agent heartbeats")
    environment_url: Optional[str] = Field(default=None, description="Environment URL used for heartbeat skill fetch")
    environment_name: Optional[str] = Field(default=None, description="Environment name for runtime context")
    allowed_environment_urls: Optional[list[str]] = Field(
        default=None,
        description="Optional additional allowlisted environment URLs for multi-env runs",
    )
    agent_tokens: Optional[Dict[str, str]] = Field(default=None, description="Per-agent auth token map")
    agent_models: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional per-agent model map keyed by agent_id (or run/agent key)",
    )

    @model_validator(mode="after")
    def validate_frequency_config(self) -> "SchedulerConfig":
        mode = str(self.frequency_mode or "fixed").strip().lower()
        self.frequency_mode = mode
        if mode not in {"fixed", "random_range"}:
            raise ValueError("frequency_mode must be one of: fixed, random_range")
        if mode == "random_range":
            if not self.heartbeat_interval_min or not self.heartbeat_interval_max:
                raise ValueError(
                    "heartbeat_interval_min and heartbeat_interval_max are required when frequency_mode=random_range"
                )
        return self


class StartSchedulerRequest(BaseModel):
    """Request to start the scheduler."""
    config: Optional[SchedulerConfig] = Field(default=None, description="Optional scheduler configuration")


def _load_agent_tokens_from_env() -> Dict[str, str]:
    """Load per-agent token map from HEARTBEAT_AGENT_TOKENS env JSON."""
    import os

    raw_tokens = os.getenv("HEARTBEAT_AGENT_TOKENS", "")
    if not raw_tokens:
        return {}

    try:
        parsed = json.loads(raw_tokens)
        if not isinstance(parsed, dict):
            return {}
        return {str(key): str(value) for key, value in parsed.items()}
    except Exception:
        return {}


def _validate_required_scheduler_context(config: SchedulerConfig) -> None:
    """Fail fast when scheduler startup lacks required auth/runtime context."""
    missing_fields = []
    if not config.environment_url:
        missing_fields.append("environment_url")
    if not config.environment_name:
        missing_fields.append("environment_name")

    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required scheduler config fields: {', '.join(missing_fields)}",
        )


class SchedulerStatusResponse(BaseModel):
    """Response containing scheduler status."""
    state: str
    tick_count: int
    start_time: Optional[str]
    uptime_seconds: float
    config: Dict[str, Any]
    agents: Dict[str, int]
    agent_states: list[Dict[str, Any]] = Field(default_factory=list)


class SchedulerProgressResponse(BaseModel):
    """Response containing lightweight scheduler progress."""
    state: str
    tick_count: int
    start_time: Optional[str]
    uptime_seconds: float
    config: Dict[str, Any]
    agents: Dict[str, int]
    progress: Dict[str, int]


@router.post("/start")
async def start_scheduler(request: Optional[StartSchedulerRequest] = None) -> Dict[str, str]:
    """Start the heartbeat scheduler.
    
    If already running, returns success without changes.
    If paused, resumes the scheduler.
    """
    current_state = heartbeat_scheduler._state
    has_runtime_config = bool(request and request.config)
    restarted_with_runtime_config = False

    if has_runtime_config:
        _validate_required_scheduler_context(request.config)
        print(
            "[DEBUG] Scheduler start config: "
            f"environment_url={request.config.environment_url}, "
            f"environment_name={request.config.environment_name}, "
            f"agent_tokens={len(request.config.agent_tokens or {})}"
        )

        # Runtime config must win over any auto-started/default scheduler state.
        if current_state in (SchedulerState.RUNNING, SchedulerState.PAUSED):
            await heartbeat_scheduler.stop()
            restarted_with_runtime_config = True

        heartbeat_scheduler.initialize(
            interval=request.config.heartbeat_interval,
            frequency_mode=request.config.frequency_mode,
            interval_min=request.config.heartbeat_interval_min,
            interval_max=request.config.heartbeat_interval_max,
            random_seed=request.config.random_seed,
            timeout=request.config.heartbeat_timeout,
            max_parallel=request.config.max_parallel_agents,
            retry_count=request.config.retry_count,
            retry_delay=request.config.retry_delay,
            jitter=request.config.jitter,
            max_heartbeats_per_agent=request.config.max_heartbeats_per_agent,
            model=request.config.model,
            environment_url=request.config.environment_url,
            environment_name=request.config.environment_name,
            allowed_environment_urls=request.config.allowed_environment_urls,
            agent_tokens=request.config.agent_tokens,
            agent_models=request.config.agent_models,
        )
        await heartbeat_scheduler.start()
        if restarted_with_runtime_config:
            return {
                "status": "restarted",
                "message": "Scheduler restarted with runtime configuration",
            }
        return {"status": "started", "message": "Heartbeat scheduler started successfully"}

    if current_state == SchedulerState.RUNNING:
        return {"status": "already_running", "message": "Scheduler is already running"}

    if current_state == SchedulerState.PAUSED:
        heartbeat_scheduler.resume()
        return {"status": "resumed", "message": "Scheduler resumed from paused state"}

    # Use defaults or environment variables
    import os
    heartbeat_scheduler.initialize(
        interval=os.getenv("HEARTBEAT_INTERVAL", "5m"),
        frequency_mode=os.getenv("HEARTBEAT_FREQUENCY_MODE", "fixed"),
        interval_min=os.getenv("HEARTBEAT_INTERVAL_MIN"),
        interval_max=os.getenv("HEARTBEAT_INTERVAL_MAX"),
        random_seed=(
            int(os.getenv("HEARTBEAT_RANDOM_SEED"))
            if os.getenv("HEARTBEAT_RANDOM_SEED")
            else None
        ),
        timeout=os.getenv("HEARTBEAT_TIMEOUT", "2m"),
        max_parallel=int(os.getenv("MAX_PARALLEL_AGENTS", "10")),
        retry_count=int(os.getenv("HEARTBEAT_RETRY_COUNT", "3")),
        retry_delay=os.getenv("HEARTBEAT_RETRY_DELAY", "10s"),
        jitter=os.getenv("HEARTBEAT_JITTER", "5s"),
        max_heartbeats_per_agent=(
            int(os.getenv("MAX_HEARTBEATS_PER_AGENT"))
            if os.getenv("MAX_HEARTBEATS_PER_AGENT")
            else None
        ),
        model=os.getenv("AGENT_MODEL", "openai/gpt-5-mini"),
        environment_url=os.getenv("HEARTBEAT_ENVIRONMENT_URL", settings.environment_url),
        environment_name=os.getenv("HEARTBEAT_ENVIRONMENT_NAME"),
        allowed_environment_urls=[
            item.strip()
            for item in str(os.getenv("HEARTBEAT_ALLOWED_ENVIRONMENT_URLS", "")).split(",")
            if item.strip()
        ] or None,
        agent_tokens=_load_agent_tokens_from_env(),
        agent_models=None,
    )

    await heartbeat_scheduler.start()
    return {"status": "started", "message": "Heartbeat scheduler started successfully"}


@router.post("/stop")
async def stop_scheduler() -> Dict[str, str]:
    """Stop the heartbeat scheduler."""
    if heartbeat_scheduler._state == SchedulerState.STOPPED:
        return {"status": "already_stopped", "message": "Scheduler is already stopped"}
    
    await heartbeat_scheduler.stop()
    return {"status": "stopped", "message": "Heartbeat scheduler stopped successfully"}


@router.post("/pause")
async def pause_scheduler() -> Dict[str, Any]:
    """Pause the heartbeat scheduler.
    
    Heartbeats will not be sent while paused.
    """
    if heartbeat_scheduler._state != SchedulerState.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause scheduler in state: {heartbeat_scheduler._state.value}"
        )
    
    cancelled_tasks = heartbeat_scheduler.pause()
    return {
        "status": "paused",
        "message": "Heartbeat scheduler paused",
        "cancelled_tasks": cancelled_tasks,
    }


@router.post("/resume")
async def resume_scheduler() -> Dict[str, str]:
    """Resume the heartbeat scheduler from paused state."""
    if heartbeat_scheduler._state != SchedulerState.PAUSED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume scheduler in state: {heartbeat_scheduler._state.value}"
        )
    
    heartbeat_scheduler.resume()
    return {"status": "resumed", "message": "Heartbeat scheduler resumed"}


@router.get("/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status(
    include_agent_states: bool = Query(
        False,
        description="Include the full per-agent state list. Disabled by default for large-run safety.",
    )
) -> SchedulerStatusResponse:
    """Get current scheduler status and configuration."""
    status = heartbeat_scheduler.get_status() if include_agent_states else heartbeat_scheduler.get_progress()
    
    return SchedulerStatusResponse(
        state=status["state"],
        tick_count=status["tick_count"],
        start_time=status["start_time"],
        uptime_seconds=status["uptime_seconds"],
        config=status["config"],
        agents=status["agents"],
        agent_states=status.get("agent_states") or [],
    )


@router.get("/progress", response_model=SchedulerProgressResponse)
async def get_scheduler_progress() -> SchedulerProgressResponse:
    """Get lightweight scheduler progress without full per-agent state payloads."""
    progress = heartbeat_scheduler.get_progress()
    return SchedulerProgressResponse(
        state=progress["state"],
        tick_count=progress["tick_count"],
        start_time=progress["start_time"],
        uptime_seconds=progress["uptime_seconds"],
        config=progress["config"],
        agents=progress["agents"],
        progress=progress["progress"],
    )


@router.get("/agents")
async def get_agent_states(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(
        default=None,
        description="Optional exact status filter, e.g. active, running, completed, failed",
    ),
) -> Dict[str, Any]:
    """Get paginated detailed state of discovered agents."""
    return heartbeat_scheduler.get_agent_states_page(
        limit=limit,
        offset=offset,
        status_filter=status,
    )
