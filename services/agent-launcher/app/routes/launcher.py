"""API routes for agent launcher."""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, List
from collections import OrderedDict
import asyncio
import hashlib
import json
import httpx

from ..config import settings
from ..agent_fs import AgentFilesystem
from ..llm_client import get_llm_client, LLMClient, LLMError
from ..simplified_social_core import (
    SIMPLIFIED_FEED_VOTE_TOOL_SUMMARIES,
    SIMPLIFIED_SOCIAL_TOOL_SUMMARIES,
)
from ..action_parser import Action, ActionType, HTTPMethod, parse_actions
from ..executor import ActionExecutor
from ..telemetry_client import record_action, ActionCategory
from ..scheduler import heartbeat_scheduler, SchedulerState
from ..heartbeat_runtime import (
    INTERACTION_MODE,
    MEMORY_MODE_LAST_N_TURNS,
    MEMORY_MODE_SESSION_TRANSCRIPT,
    MEMORY_MODE_STATELESS,
    PROMPT_CONTRACT_VERSION,
    StopReason,
    TurnOrchestrator,
    build_turn_memory_message,
    evaluate_gating,
    resolve_heartbeat_policy,
)


router = APIRouter()
def _tool_summaries_for_agent_core(agent_core: str) -> Optional[Dict[str, str]]:
    normalized = str(agent_core or "").strip().lower()
    if normalized == "simplified_social_core":
        return SIMPLIFIED_SOCIAL_TOOL_SUMMARIES
    if normalized == "openclaw_py_minimal_core":
        return SIMPLIFIED_FEED_VOTE_TOOL_SUMMARIES
    return None


# Request/Response Models

class HeartbeatRequest(BaseModel):
    """Request model for heartbeat endpoint."""
    agent_id: str = Field(..., description="Unique identifier for the agent")
    run_id: Optional[str] = Field(
        "default",
        description="Run ID for organizing agents",
    )
    environment_url: Optional[str] = Field(
        None,
        description="URL to fetch skill.md from"
    )
    allowed_environment_urls: Optional[List[str]] = Field(
        None,
        description="Optional additional allowed environment URLs for multi-env runs"
    )
    environment_auth_registry_seed: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional host-keyed auth seed injected by scheduler/bootstrap",
    )
    environment_name: Optional[str] = Field(
        None,
        description="Environment name for runtime context"
    )
    agent_token: Optional[str] = Field(
        None,
        description="Authentication token for the agent"
    )
    user_message: Optional[str] = Field(
        None,
        description="Optional debug override for heartbeat prompt (policy prompt is default)"
    )
    openrouter_api_key: Optional[str] = Field(
        None,
        description="OpenRouter API key for LLM calls (overrides environment variable)"
    )
    model: Optional[str] = Field(
        None,
        description="Model to use for LLM calls (e.g., openai/gpt-5-mini)"
    )
    tick: Optional[int] = Field(
        None,
        description="Scheduler tick number for this heartbeat call"
    )
    heartbeat_index: Optional[int] = Field(
        None,
        description="Per-agent heartbeat index for this call"
    )


class HeartbeatResponse(BaseModel):
    """Response model for heartbeat endpoint."""
    agent_id: str
    status: str
    actions_executed: int
    results: List[Dict[str, Any]]
    summary: str
    llm_cost: float = Field(default=0.0, description="Cost of LLM API call in USD")
    stop_reason: str = Field(
        default=StopReason.HEARTBEAT_OK,
        description="Terminal reason for ending the heartbeat turn",
    )
    rounds_executed: int = Field(default=1, description="Number of model rounds executed in this heartbeat")
    model_calls: int = Field(default=1, description="Number of model calls performed in this heartbeat")
    elapsed_ms: int = Field(default=0, description="Total heartbeat turn duration in milliseconds")
    round_details: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Round-level execution details and telemetry metadata",
    )
    prompt_contract_version: Optional[str] = Field(
        default=None,
        description="Prompt contract version used for this heartbeat tick",
    )
    interaction_mode: Optional[str] = Field(
        default=None,
        description="Interaction mode used for this heartbeat tick",
    )
    policy_source: Optional[str] = Field(
        default=None,
        description="Resolved heartbeat policy source for this tick",
    )
    memory_mode: Optional[str] = Field(
        default=None,
        description="Resolved memory mode used for this heartbeat tick",
    )
    memory_turns_loaded: int = Field(
        default=0,
        description="Number of memory turns loaded into this heartbeat tick",
    )
    agent_core: Optional[str] = Field(
        default=None,
        description="Resolved inner agent runtime path used for this heartbeat tick",
    )
    heartbeat_index: int = Field(
        default=0,
        description="Per-agent heartbeat index used for this heartbeat response",
    )
    error: Optional[Dict[str, Any]] = Field(default=None, description="Error information if heartbeat failed")


class CreateAgentRequest(BaseModel):
    """Request model for creating a new agent."""
    model_config = ConfigDict(extra="ignore")

    agent_id: str = Field(..., description="Unique identifier for the agent")
    agent_name: Optional[str] = Field(None, description="Optional display name used for runtime package rendering")
    run_id: Optional[str] = Field("default", description="Run ID for organizing agents")
    runtime_id: Optional[str] = Field("openclaw", description="Reusable runtime package identifier")
    runtime_content_hash: Optional[str] = Field(
        None,
        description="Optional expected content hash for traceability",
    )
    agents: Optional[str] = Field(None, description="Content for AGENTS.md")
    identity: Optional[str] = Field(None, description="Content for IDENTITY.md")
    soul: Optional[str] = Field(None, description="Content for SOUL.md")
    tools: Optional[str] = Field(None, description="Content for TOOLS.md")
    bootstrap: Optional[str] = Field(None, description="Content for BOOTSTRAP.md")
    user: Optional[str] = Field(None, description="Content for USER.md")
    heartbeat: Optional[str] = Field(None, description="Content for HEARTBEAT.md")
    environment_url: Optional[str] = Field(None, description="Primary environment URL used for runtime package rendering")
    environment_name: Optional[str] = Field(None, description="Environment name for skill bootstrap (e.g., 'myenv')")

class CreateAgentResponse(BaseModel):
    """Response model for agent creation."""
    agent_id: str
    run_id: str
    created: bool
    path: str
    runtime_resolution: Optional[Dict[str, Any]] = Field(
        None,
        description="Resolved runtime package source details used for agent materialization",
    )
    environment_bootstrap: Optional[Dict[str, Any]] = Field(None, description="Environment skill bootstrap result")
    bootstrap_validation: Optional[Dict[str, Any]] = Field(None, description="BOOTSTRAP.md validation result")
    render_warnings: Optional[List[Dict[str, Any]]] = Field(None, description="Runtime package render warnings")


class CreateAgentsBatchRequest(BaseModel):
    """Request model for batch agent creation."""
    agents: List[CreateAgentRequest] = Field(default_factory=list, description="Agents to materialize")
    concurrency: Optional[int] = Field(
        default=None,
        ge=1,
        le=500,
        description="Optional per-request materialization concurrency cap",
    )


class CreateAgentBatchResult(BaseModel):
    """Per-agent result for batch agent creation."""
    agent_id: str
    run_id: str
    created: bool
    path: Optional[str] = None
    runtime_resolution: Optional[Dict[str, Any]] = None
    environment_bootstrap: Optional[Dict[str, Any]] = None
    bootstrap_validation: Optional[Dict[str, Any]] = None
    render_warnings: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class CreateAgentsBatchResponse(BaseModel):
    """Response model for batch agent creation."""
    requested_count: int
    created_count: int
    error_count: int
    results: List[CreateAgentBatchResult]


class AgentInfoResponse(BaseModel):
    """Response model for agent info."""
    agent_id: str
    run_id: str
    path: str
    workspace_path: str
    skills_path: str
    exists: bool
    files: Dict[str, Any]
    bootstrap_complete: bool


class DeleteAgentResponse(BaseModel):
    """Response model for agent deletion."""
    agent_id: str
    deleted: bool


class InstallSkillRequest(BaseModel):
    """Request model for installing a skill."""
    environment_url: Optional[str] = Field(
        None,
        description="URL to fetch skill files from"
    )
    skill_files: Optional[Dict[str, str]] = Field(
        None,
        description="Optional pre-fetched skill files (SKILL.md, HEARTBEAT.md, MESSAGING.md, RULES.md)"
    )


class InstallSkillResponse(BaseModel):
    """Response model for skill installation."""
    agent_id: str
    skill_name: str
    success: bool
    installed_files: List[str]
    errors: List[str]
    skill_path: str


class ListSkillsResponse(BaseModel):
    """Response model for listing installed skills."""
    agent_id: str
    skills: List[str]
    skills_path: str


# Helper Functions

async def fetch_skill(environment_url: Optional[str]) -> Optional[str]:
    """Fetch skill.md from environment URL."""
    if not environment_url:
        # Try to use configured environment URL
        environment_url = settings.environment_url
    
    if not environment_url:
        return None
    
    skill_url = environment_url.rstrip("/") + "/skill.md"
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(skill_url)
            if response.status_code == 200:
                return response.text
    except Exception:
        pass
    
    return None


def _extract_local_api_token(payload: Any) -> Optional[str]:
    """Extract API token from a locally persisted credential payload."""
    if not isinstance(payload, dict):
        return None
    direct_fields = ("api_token", "api_key", "token", "access_token")
    for field in direct_fields:
        raw = payload.get(field)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    agent_payload = payload.get("agent")
    if isinstance(agent_payload, dict):
        for field in direct_fields:
            raw = agent_payload.get(field)
            if raw is not None and str(raw).strip():
                return str(raw).strip()
    return None


async def _load_environment_auth_registry_seed(
    agent_fs: AgentFilesystem,
    *,
    environment_name: Optional[str],
    environment_url: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Load host-scoped auth seed from persisted credentials."""
    if not environment_name or not environment_url:
        return None

    credentials_path = f"skills/{environment_name}/state/credentials.json"
    raw_credentials = await agent_fs.read_file(credentials_path)
    source = "local_skill_state"
    if not raw_credentials:
        workspace_credentials_path = f".{environment_name}-credentials.json"
        raw_credentials = await agent_fs.read_file(workspace_credentials_path)
        source = "workspace_credentials"
    if not raw_credentials:
        return None

    try:
        parsed = json.loads(raw_credentials)
    except Exception:
        return None

    token = _extract_local_api_token(parsed)
    if not token:
        return None

    return {
        "entries": {
            str(environment_url): {
                "api_token": token,
                "source": source,
            }
        }
    }


def _build_platform_probe_actions(
    environment_url: str,
    *,
    action_prefix: str,
    max_actions: int = 1,
) -> List[Action]:
    """Build deterministic platform-generic reads for fallback/bootstrap paths.

    Note: this must remain environment-agnostic. Prefer `/contract` and reserve
    `/health` as emergency fallback only.
    """
    base_url = environment_url.rstrip("/")
    actions: List[Action] = []
    actions.append(
        Action(
            action=ActionType.HTTP,
            action_name=f"{action_prefix}_get_contract",
            method=HTTPMethod.GET,
            url=f"{base_url}/contract",
            description="Read environment contract (platform-generic fallback probe)",
        )
    )
    return actions[:max_actions]


def _build_health_fallback_action(
    environment_url: str,
    *,
    action_name: str,
    description: str,
) -> Action:
    """Build emergency-only health probe action."""
    return Action(
        action=ActionType.HTTP,
        action_name=action_name,
        method=HTTPMethod.GET,
        url=f"{environment_url.rstrip('/')}/health",
        description=description,
    )


def _build_allowed_environment_urls(
    primary_environment_url: Optional[str],
    additional_environment_urls: Optional[List[str]],
) -> List[str]:
    """Build unique ordered environment URL allowlist."""
    ordered_urls: List[str] = []
    for raw in [primary_environment_url, *(additional_environment_urls or [])]:
        if not raw:
            continue
        url = str(raw).strip()
        if not url or url in ordered_urls:
            continue
        ordered_urls.append(url)
    return ordered_urls


PROMPT_PART_DEDUPE_MAX_ITEMS = 5000
_PROMPT_PART_SENT: "OrderedDict[str, bool]" = OrderedDict()
PROMPT_PART_CONTENT_PREVIEW_MAX_CHARS = 512
PARSE_ERROR_ALARM_RATE_THRESHOLD = 0.25
PARSE_ERROR_ALARM_MIN_ROUNDS = 3
PARSE_ERROR_ALARM_ACTION_TYPE = "heartbeat_parse_error_alarm"


def _remember_prompt_part(cache_key: str) -> None:
    """Remember emitted prompt-part key with bounded cache eviction."""
    if cache_key in _PROMPT_PART_SENT:
        _PROMPT_PART_SENT.move_to_end(cache_key)
    else:
        _PROMPT_PART_SENT[cache_key] = True
    while len(_PROMPT_PART_SENT) > PROMPT_PART_DEDUPE_MAX_ITEMS:
        _PROMPT_PART_SENT.popitem(last=False)


def _action_summary(action: Action) -> Dict[str, Any]:
    """Compact, stable action summary for telemetry/UI."""
    try:
        if hasattr(action, "model_dump"):
            data = action.model_dump()
        elif hasattr(action, "dict"):
            data = action.dict()
        else:
            data = {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    keep_keys = ("action", "action_name", "description", "method", "url", "filename", "path")
    summary = {key: data.get(key) for key in keep_keys if data.get(key) not in (None, "")}

    # Ensure enums/custom objects are JSON-safe.
    for key, value in list(summary.items()):
        if isinstance(value, (str, int, float, bool)) or value is None:
            continue
        summary[key] = str(value)

    return summary


def _coerce_nonnegative_int(*values: Any) -> int:
    """Pick first parseable non-negative integer from candidate values."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return 0


def _coerce_nonnegative_float(*values: Any) -> float:
    """Pick first parseable non-negative float from candidate values."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return 0.0


# API Endpoints

def _parts_manifest(system_prompt_parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    manifest: List[Dict[str, Any]] = []
    for part in system_prompt_parts:
        entry = {
            "name": part.get("name"),
            "kind": part.get("kind"),
            "sha256": part.get("sha256"),
            "bytes": part.get("bytes"),
            "dynamic": bool(part.get("dynamic")),
            "truncated": bool(part.get("truncated")),
            "truncation_reason": part.get("truncation_reason"),
        }
        if part.get("kind") == "available_skills":
            entry["skills_count"] = int(part.get("skills_count") or 0)
            entry["skills_total"] = int(part.get("skills_total") or 0)
        entry["content"] = part.get("content", "")
        manifest.append(entry)
    return manifest


async def _emit_prompt_parts(
    *,
    run_id: str,
    agent_id: str,
    tick: Optional[int],
    heartbeat_index: Optional[int],
    environment_url: Optional[str],
    environment_name: Optional[str],
    policy_source: str,
    agent_core: Optional[str],
    system_prompt_parts: List[Dict[str, Any]],
) -> None:
    """Emit static prompt parts once per run hash."""
    for part in system_prompt_parts:
        if part.get("dynamic"):
            continue
        sha256 = part.get("sha256")
        if not sha256:
            continue

        cache_key = f"{run_id}:{sha256}"
        if cache_key in _PROMPT_PART_SENT:
            continue
        _remember_prompt_part(cache_key)

        try:
            raw_content = str(part.get("content", ""))
            content_preview = raw_content[:PROMPT_PART_CONTENT_PREVIEW_MAX_CHARS]
            content_truncated = len(raw_content) > PROMPT_PART_CONTENT_PREVIEW_MAX_CHARS
            await record_action(
                run_id=run_id,
                agent_id=agent_id,
                action_type="prompt_part",
                action_category=ActionCategory.SYSTEM.value,
                success=True,
                duration_ms=0,
                payload={
                    "event_type": "prompt_part",
                    "tick": tick,
                    "heartbeat_index": heartbeat_index,
                    "environment_url": environment_url,
                    "environment_name": environment_name,
                    "part_name": part.get("name"),
                    "part_kind": part.get("kind"),
                    "part_sha256": sha256,
                    "part_bytes": part.get("bytes"),
                    "part_truncated": bool(part.get("truncated")),
                    "part_truncation_reason": part.get("truncation_reason"),
                    "skills_count": int(part.get("skills_count") or 0)
                    if part.get("kind") == "available_skills"
                    else None,
                    "skills_total": int(part.get("skills_total") or 0)
                    if part.get("kind") == "available_skills"
                    else None,
                    "content": raw_content,
                    "content_preview": content_preview,
                    "content_preview_truncated": content_truncated,
                    "prompt_contract_version": PROMPT_CONTRACT_VERSION,
                    "interaction_mode": INTERACTION_MODE,
                    "policy_source": policy_source,
                    "agent_core": agent_core,
                },
            )
        except Exception as exc:
            print(
                "[WARNING] TELEMETRY_FAILED prompt_part "
                f"run_id={run_id} agent_id={agent_id} error={exc}"
            )


async def _emit_llm_rounds(
    *,
    run_id: str,
    agent_id: str,
    tick: Optional[int],
    heartbeat_index: Optional[int],
    environment_url: Optional[str],
    environment_name: Optional[str],
    resolved_model: Optional[str],
    policy_source: str,
    agent_core: Optional[str],
    base_user_message: str,
    system_prompt_parts: List[Dict[str, Any]],
    round_details: List[Dict[str, Any]],
) -> None:
    """Emit llm_io telemetry for each round."""
    parts_manifest = _parts_manifest(system_prompt_parts)
    available_skills_count = 0
    for part in system_prompt_parts:
        if part.get("kind") == "available_skills":
            available_skills_count = int(part.get("skills_count") or 0)
            break
    for detail in round_details:
        try:
            llm_tokens_input = _coerce_nonnegative_int(
                detail.get("llm_tokens_input"),
                (detail.get("usage") or {}).get("prompt_tokens"),
                (detail.get("usage") or {}).get("input_tokens"),
            )
            llm_tokens_output = _coerce_nonnegative_int(
                detail.get("llm_tokens_output"),
                (detail.get("usage") or {}).get("completion_tokens"),
                (detail.get("usage") or {}).get("output_tokens"),
            )
            llm_cost = _coerce_nonnegative_float(
                detail.get("llm_cost_usd"),
                (detail.get("usage") or {}).get("total_cost"),
            )

            await record_action(
                run_id=run_id,
                agent_id=agent_id,
                action_type="llm_io",
                action_category=ActionCategory.SYSTEM.value,
                success=(detail.get("llm_error") is None and detail.get("parse_error") is None),
                duration_ms=0,
                payload={
                    "event_type": "llm_io",
                    "tick": tick,
                    "heartbeat_index": heartbeat_index,
                    "round_index": detail.get("round_index"),
                    "memory_mode": detail.get("memory_mode"),
                    "memory_turns_loaded": detail.get("memory_turns_loaded"),
                    "environment_url": environment_url,
                    "environment_name": environment_name,
                    "model": resolved_model,
                    "system_prompt_parts": parts_manifest,
                    "available_skills_count": available_skills_count,
                    "user_message": base_user_message,
                    "observation_message": detail.get("observation_message"),
                    "response_text": detail.get("response_text"),
                    "response_sha256": detail.get("response_sha256"),
                    "parsed_action_count": detail.get("parsed_action_count", 0),
                    "actionable_action_count": detail.get("actionable_action_count", 0),
                    "actions": detail.get("actions", []),
                    "observations": detail.get("observations", []),
                    "stop_reason": detail.get("stop_reason"),
                    "prompt_contract_version": PROMPT_CONTRACT_VERSION,
                    "interaction_mode": INTERACTION_MODE,
                    "policy_source": policy_source,
                    "agent_core": agent_core,
                    "llm_error": detail.get("llm_error"),
                    "parse_error": detail.get("parse_error"),
                    "usage": detail.get("usage", {}),
                    "reasoning": detail.get("reasoning", {}),
                    "llm_cost_usd": llm_cost,
                    "llm_tokens_input": llm_tokens_input,
                    "llm_tokens_output": llm_tokens_output,
                },
                llm_tokens_input=llm_tokens_input,
                llm_tokens_output=llm_tokens_output,
                llm_cost_usd=llm_cost,
            )
        except Exception as exc:
            print(
                "[WARNING] TELEMETRY_FAILED llm_io "
                f"run_id={run_id} agent_id={agent_id} round={detail.get('round_index')} error={exc}"
            )


def _parse_error_stats(round_details: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute parse-error counters/rate from round details."""
    total_rounds = max(len(round_details), 0)
    parse_error_rounds = sum(1 for detail in round_details if detail.get("parse_error"))
    parse_error_rate = (parse_error_rounds / total_rounds) if total_rounds > 0 else 0.0
    return {
        "total_rounds": total_rounds,
        "parse_error_rounds": parse_error_rounds,
        "parse_error_rate": parse_error_rate,
    }


async def _emit_parse_error_rate_alarm(
    *,
    run_id: str,
    agent_id: str,
    tick: Optional[int],
    heartbeat_index: Optional[int],
    environment_url: Optional[str],
    environment_name: Optional[str],
    resolved_model: Optional[str],
    policy_source: str,
    round_details: List[Dict[str, Any]],
    rate_threshold: float = PARSE_ERROR_ALARM_RATE_THRESHOLD,
    min_rounds: int = PARSE_ERROR_ALARM_MIN_ROUNDS,
) -> None:
    """Emit alarm telemetry when parse-error rate exceeds threshold."""
    stats = _parse_error_stats(round_details)
    total_rounds = int(stats["total_rounds"])
    parse_error_rounds = int(stats["parse_error_rounds"])
    parse_error_rate = float(stats["parse_error_rate"])
    if total_rounds < max(min_rounds, 1):
        return
    if parse_error_rounds <= 0:
        return
    if parse_error_rate < max(min(rate_threshold, 1.0), 0.0):
        return

    payload = {
        "event_type": PARSE_ERROR_ALARM_ACTION_TYPE,
        "tick": tick,
        "heartbeat_index": heartbeat_index,
        "environment_url": environment_url,
        "environment_name": environment_name,
        "model": resolved_model,
        "policy_source": policy_source,
        "parse_error_rate": parse_error_rate,
        "parse_error_rounds": parse_error_rounds,
        "total_rounds": total_rounds,
        "threshold": rate_threshold,
        "min_rounds": min_rounds,
    }
    try:
        await record_action(
            run_id=run_id,
            agent_id=agent_id,
            action_type=PARSE_ERROR_ALARM_ACTION_TYPE,
            action_category=ActionCategory.SYSTEM.value,
            success=False,
            duration_ms=0,
            payload=payload,
            error_message=(
                "High parse_error rate during heartbeat turn: "
                f"{parse_error_rounds}/{total_rounds} ({parse_error_rate:.3f})"
            ),
        )
    except Exception as exc:
        print(
            "[WARNING] TELEMETRY_FAILED parse_error_alarm "
            f"run_id={run_id} agent_id={agent_id} error={exc}"
        )


async def _build_heartbeat_llm_client(
    request: HeartbeatRequest,
    *,
    resolved_model: Optional[str],
) -> LLMClient:
    """Resolve LLM client with request overrides."""
    if request.openrouter_api_key:
        return LLMClient(provider="openrouter", model=resolved_model, api_key=request.openrouter_api_key)
    if resolved_model:
        return LLMClient(model=resolved_model)
    return await get_llm_client()


async def _heartbeat_paused_response(request: HeartbeatRequest, agent_fs: AgentFilesystem) -> HeartbeatResponse:
    summary = "Heartbeat skipped: scheduler is paused"
    await agent_fs.update_heartbeat("paused", summary)
    return HeartbeatResponse(
        agent_id=request.agent_id,
        status="paused",
        actions_executed=0,
        results=[],
        summary=summary,
        llm_cost=0.0,
        stop_reason=StopReason.GATED_SKIP,
        rounds_executed=0,
        model_calls=0,
        elapsed_ms=0,
        round_details=[],
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        interaction_mode=INTERACTION_MODE,
        policy_source="scheduler_paused",
        memory_mode=MEMORY_MODE_STATELESS,
        memory_turns_loaded=0,
        agent_core=None,
        heartbeat_index=_coerce_nonnegative_int(request.heartbeat_index),
    )


async def _heartbeat_llm_error_response(
    request: HeartbeatRequest,
    agent_fs: AgentFilesystem,
    exc: LLMError,
) -> HeartbeatResponse:
    error_dict = exc.to_dict()
    await agent_fs.update_heartbeat("error", error_dict["message"])
    return HeartbeatResponse(
        agent_id=request.agent_id,
        status="error",
        actions_executed=0,
        results=[],
        summary=f"LLM Error: {error_dict['message']}",
        llm_cost=0.0,
        stop_reason=StopReason.ERROR,
        rounds_executed=0,
        model_calls=0,
        elapsed_ms=0,
        round_details=[],
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        interaction_mode=INTERACTION_MODE,
        policy_source=None,
        memory_mode=MEMORY_MODE_STATELESS,
        memory_turns_loaded=0,
        agent_core=None,
        heartbeat_index=_coerce_nonnegative_int(request.heartbeat_index),
        error=error_dict,
    )


async def _heartbeat_execution_error_response(
    request: HeartbeatRequest,
    agent_fs: AgentFilesystem,
    exc: Exception,
) -> HeartbeatResponse:
    error_msg = str(exc)
    await agent_fs.update_heartbeat("error", error_msg)
    return HeartbeatResponse(
        agent_id=request.agent_id,
        status="error",
        actions_executed=0,
        results=[],
        summary=f"Execution Error: {error_msg}",
        llm_cost=0.0,
        stop_reason=StopReason.ERROR,
        rounds_executed=0,
        model_calls=0,
        elapsed_ms=0,
        round_details=[],
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        interaction_mode=INTERACTION_MODE,
        policy_source=None,
        memory_mode=MEMORY_MODE_STATELESS,
        memory_turns_loaded=0,
        agent_core=None,
        heartbeat_index=_coerce_nonnegative_int(request.heartbeat_index),
        error={"message": error_msg, "type": "execution_error"},
    )


async def _run_heartbeat_turn(
    request: HeartbeatRequest,
    agent_fs: AgentFilesystem,
) -> HeartbeatResponse:
    bootstrap_validation = await agent_fs.validate_bootstrap_reference()
    if bootstrap_validation.get("warnings"):
        print(
            "[WARNING] BOOTSTRAP validation warnings for "
            f"{request.agent_id}: {bootstrap_validation['warnings']}"
        )

    installed_skills = await agent_fs.get_installed_skills()
    agent_config = await agent_fs.load_agent_config()
    runtime_block = agent_config.get("runtime") if isinstance(agent_config.get("runtime"), dict) else {}
    agent_core = str(runtime_block.get("agent_core") or "").strip()
    if not installed_skills and agent_core not in {"simplified_social_core", "openclaw_py_minimal_core"}:
        error_dict = {
            "message": (
                "No installed skills found for agent. "
                "Expected at least one environment skill before heartbeat execution."
            ),
            "type": "bootstrap_error",
        }
        await agent_fs.update_heartbeat("error", error_dict["message"])
        return HeartbeatResponse(
            agent_id=request.agent_id,
            status="error",
            actions_executed=0,
            results=[],
            summary=f"Bootstrap Error: {error_dict['message']}",
            llm_cost=0.0,
            stop_reason=StopReason.ERROR,
            rounds_executed=0,
            model_calls=0,
            elapsed_ms=0,
            round_details=[],
            prompt_contract_version=PROMPT_CONTRACT_VERSION,
            interaction_mode=INTERACTION_MODE,
            policy_source="bootstrap_validation",
            memory_mode=MEMORY_MODE_STATELESS,
            memory_turns_loaded=0,
            agent_core=None,
            error=error_dict,
        )

    if settings.llm_provider.lower() == "openrouter" and not (
        request.openrouter_api_key or settings.openrouter_api_key
    ):
        error_dict = {
            "message": (
                "Missing OpenRouter API key for heartbeat execution. "
                "Provide request.openrouter_api_key or configure "
                "OPENROUTER_API_KEY / AGENT_LAUNCHER_OPENROUTER_API_KEY."
            ),
            "type": "configuration_error",
        }
        await agent_fs.update_heartbeat("error", error_dict["message"])
        return HeartbeatResponse(
            agent_id=request.agent_id,
            status="error",
            actions_executed=0,
            results=[],
            summary=f"Configuration Error: {error_dict['message']}",
            llm_cost=0.0,
            stop_reason=StopReason.ERROR,
            rounds_executed=0,
            model_calls=0,
            elapsed_ms=0,
            round_details=[],
            prompt_contract_version=PROMPT_CONTRACT_VERSION,
            interaction_mode=INTERACTION_MODE,
            policy_source="configuration_validation",
            memory_mode=MEMORY_MODE_STATELESS,
            memory_turns_loaded=0,
            agent_core=None,
            error=error_dict,
        )

    run_id = request.run_id or settings.run_id or "default"
    effective_environment_url = request.environment_url or settings.environment_url
    if not effective_environment_url and request.allowed_environment_urls:
        effective_environment_url = next(
            (url for url in request.allowed_environment_urls if isinstance(url, str) and url.strip()),
            None,
        )
    allowed_environment_urls = _build_allowed_environment_urls(
        effective_environment_url,
        request.allowed_environment_urls,
    )

    policy_resolution = resolve_heartbeat_policy(agent_fs.agent_path)
    policy = policy_resolution.policy
    heartbeat_content = await agent_fs.read_file("HEARTBEAT.md")
    scheduler_snapshot = (heartbeat_scheduler.get_status().get("config") or {})
    gate = evaluate_gating(
        policy,
        heartbeat_content=heartbeat_content,
        scheduler_status=scheduler_snapshot,
    )
    memory_entries: List[Any] = []
    memory_message: Optional[str] = None
    session_messages: List[Dict[str, str]] = []
    memory_mode = policy.memory_mode
    if memory_mode == MEMORY_MODE_LAST_N_TURNS and policy.memory_turn_window > 0:
        memory_entries = await agent_fs.get_recent_heartbeat_entries(policy.memory_turn_window)
        memory_message = build_turn_memory_message(
            memory_entries,
            max_chars=policy.memory_max_chars,
        )
    elif memory_mode == MEMORY_MODE_SESSION_TRANSCRIPT and policy.memory_turn_window > 0:
        session_messages = await agent_fs.get_recent_session_messages(
            policy.memory_turn_window,
            max_chars=policy.memory_max_chars,
        )
        memory_entries = list(session_messages)
    else:
        memory_mode = MEMORY_MODE_STATELESS

    if gate.should_skip:
        summary = gate.summary or "Heartbeat skipped by policy gate"
        await agent_fs.update_heartbeat("skipped", summary)
        return HeartbeatResponse(
            agent_id=request.agent_id,
            status="skipped",
            actions_executed=0,
            results=[],
            summary=summary,
            llm_cost=0.0,
            stop_reason=gate.reason or StopReason.GATED_SKIP,
            rounds_executed=0,
            model_calls=0,
            elapsed_ms=0,
            round_details=[
                {
                    "stop_reason": gate.reason or StopReason.GATED_SKIP,
                    "gate": gate.metadata,
                    "prompt_contract_version": PROMPT_CONTRACT_VERSION,
                    "interaction_mode": INTERACTION_MODE,
                    "policy_source": policy_resolution.source,
                    "memory_mode": memory_mode,
                    "memory_turns_loaded": len(memory_entries),
                    "agent_core": policy.agent_core,
                    "heartbeat_index": _coerce_nonnegative_int(request.heartbeat_index),
                }
            ],
            prompt_contract_version=PROMPT_CONTRACT_VERSION,
            interaction_mode=INTERACTION_MODE,
            policy_source=policy_resolution.source,
            memory_mode=memory_mode,
            memory_turns_loaded=len(memory_entries),
            agent_core=policy.agent_core,
            heartbeat_index=_coerce_nonnegative_int(request.heartbeat_index),
        )

    resolved_user_message = (request.user_message or policy.prompt).strip() or policy.prompt

    runtime_context = {
        "environment_url": effective_environment_url,
        "environment_name": request.environment_name,
        "run_id": run_id,
        "agent_id": request.agent_id,
        "tick": request.tick,
        "heartbeat_index": request.heartbeat_index,
        "interaction_mode": INTERACTION_MODE,
        "prompt_contract_version": PROMPT_CONTRACT_VERSION,
        "memory_mode": memory_mode,
        "memory_turns_loaded": len(memory_entries),
        "heartbeat_prompt": resolved_user_message,
    }
    if policy.agent_core in {"simplified_social_core", "openclaw_py_minimal_core"}:
        system_prompt, system_prompt_parts = await agent_fs.assemble_system_prompt_with_parts(
            skill_content=None,
            manifest_content=None,
            runtime_context=runtime_context,
            heartbeat_tail_chars=policy.heartbeat_tail_chars,
            max_skill_chars=policy.max_skill_chars,
            max_prompt_chars=policy.max_prompt_chars,
            single_skill_mode=policy.single_skill_mode,
            preferred_skill_name=request.environment_name,
            tool_summaries=_tool_summaries_for_agent_core(policy.agent_core),
            treat_heartbeat_as_dynamic=False,
        )
    else:
        system_prompt, system_prompt_parts = await agent_fs.assemble_system_prompt_with_parts(
            skill_content=None,
            manifest_content=None,
            runtime_context=runtime_context,
            heartbeat_tail_chars=policy.heartbeat_tail_chars,
            max_skill_chars=policy.max_skill_chars,
            max_prompt_chars=policy.max_prompt_chars,
            single_skill_mode=policy.single_skill_mode,
            preferred_skill_name=request.environment_name,
        )

    await _emit_prompt_parts(
        run_id=run_id,
        agent_id=request.agent_id,
        tick=request.tick,
        heartbeat_index=request.heartbeat_index,
        environment_url=effective_environment_url,
        environment_name=request.environment_name,
        policy_source=policy_resolution.source,
        agent_core=policy.agent_core,
        system_prompt_parts=system_prompt_parts,
    )

    resolved_model = request.model or policy.model or None
    llm_client = await _build_heartbeat_llm_client(
        request,
        resolved_model=resolved_model,
    )

    executor = ActionExecutor(
        agent_fs,
        agent_id=request.agent_id,
        run_id=run_id,
        heartbeat_tick=request.tick,
        heartbeat_index=request.heartbeat_index,
        environment_name=request.environment_name,
        primary_environment_url=effective_environment_url,
        allowed_environment_urls=allowed_environment_urls or None,
        agent_token=request.agent_token,
        environment_auth_registry_seed=await _load_environment_auth_registry_seed(
            agent_fs,
            environment_name=request.environment_name,
            environment_url=effective_environment_url,
        ),
    )

    try:
        orchestrator = TurnOrchestrator(
            agent_id=request.agent_id,
            run_id=run_id,
            tick=request.tick,
            policy=policy,
            system_prompt=system_prompt,
            user_prompt=resolved_user_message,
            llm_client=llm_client,
            executor=executor,
            initial_messages=session_messages,
            memory_message=memory_message,
            memory_mode=memory_mode,
            memory_turns_loaded=len(memory_entries),
            round_zero_fallback=None,
        )
        turn_result = await orchestrator.run()
    finally:
        await executor.close()

    if memory_mode == MEMORY_MODE_SESSION_TRANSCRIPT and policy.memory_turn_window > 0:
        await agent_fs.store_session_messages(
            turn_result.transcript_messages,
            limit=policy.memory_turn_window,
            max_chars=policy.memory_max_chars,
        )

    await _emit_llm_rounds(
        run_id=run_id,
        agent_id=request.agent_id,
        tick=request.tick,
        heartbeat_index=request.heartbeat_index,
        environment_url=effective_environment_url,
        environment_name=request.environment_name,
        resolved_model=resolved_model or getattr(llm_client, "model", None),
        policy_source=policy_resolution.source,
        agent_core=policy.agent_core,
        base_user_message=resolved_user_message,
        system_prompt_parts=system_prompt_parts,
        round_details=turn_result.round_details,
    )
    await _emit_parse_error_rate_alarm(
        run_id=run_id,
        agent_id=request.agent_id,
        tick=request.tick,
        heartbeat_index=request.heartbeat_index,
        environment_url=effective_environment_url,
        environment_name=request.environment_name,
        resolved_model=resolved_model or getattr(llm_client, "model", None),
        policy_source=policy_resolution.source,
        round_details=turn_result.round_details,
    )

    heartbeat_status = "error" if turn_result.status == "error" else "completed"
    await agent_fs.update_heartbeat(heartbeat_status, turn_result.summary)

    summary = turn_result.summary
    bootstrap_completed = False
    if heartbeat_status != "error":
        bootstrap_completed = await agent_fs.complete_bootstrap()
    if bootstrap_completed:
        summary += " (bootstrap completed)"

    return HeartbeatResponse(
        agent_id=request.agent_id,
        status=heartbeat_status,
        actions_executed=turn_result.actions_executed,
        results=turn_result.results,
        summary=summary,
        llm_cost=turn_result.llm_cost,
        stop_reason=turn_result.stop_reason,
        rounds_executed=turn_result.rounds_executed,
        model_calls=turn_result.model_calls,
        elapsed_ms=turn_result.elapsed_ms,
        round_details=turn_result.round_details,
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        interaction_mode=INTERACTION_MODE,
        policy_source=policy_resolution.source,
        memory_mode=memory_mode,
        memory_turns_loaded=len(memory_entries),
        agent_core=policy.agent_core,
        heartbeat_index=_coerce_nonnegative_int(request.heartbeat_index),
        error=turn_result.error,
    )


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(request: HeartbeatRequest) -> HeartbeatResponse:
    """Trigger an agent heartbeat turn with bounded multi-round orchestration."""
    agent_fs = AgentFilesystem(request.agent_id)
    if not await agent_fs.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{request.agent_id}' not found")
    if heartbeat_scheduler._state == SchedulerState.PAUSED:
        return await _heartbeat_paused_response(request, agent_fs)
    try:
        return await _run_heartbeat_turn(request, agent_fs)
    except LLMError as exc:
        return await _heartbeat_llm_error_response(request, agent_fs, exc)
    except Exception as exc:
        return await _heartbeat_execution_error_response(request, agent_fs, exc)


def _build_create_agent_initial_content(request: CreateAgentRequest) -> Dict[str, str]:
    """Build optional workspace file overrides for agent materialization."""
    initial_content: Dict[str, str] = {}
    if request.agents:
        initial_content["AGENTS.md"] = request.agents
    if request.identity:
        initial_content["IDENTITY.md"] = request.identity
    if request.soul:
        initial_content["SOUL.md"] = request.soul
    if request.tools:
        initial_content["TOOLS.md"] = request.tools
    if request.bootstrap:
        initial_content["BOOTSTRAP.md"] = request.bootstrap
    if request.user:
        initial_content["USER.md"] = request.user
    if request.heartbeat:
        initial_content["HEARTBEAT.md"] = request.heartbeat
    return initial_content


async def _create_agent_from_request(request: CreateAgentRequest) -> CreateAgentResponse:
    """Create a new agent filesystem from a validated request."""
    print(f"[DEBUG] Received POST /agents request for agent_id={request.agent_id}, run_id={request.run_id}")
    agent_fs = AgentFilesystem(request.agent_id, request.run_id or "default")
    print(f"[DEBUG] Agent filesystem path: {agent_fs.agent_path}")

    exists = await agent_fs.exists()
    print(f"[DEBUG] Agent directory exists: {exists}")
    if exists:
        print(f"[ERROR] Agent '{request.agent_id}' already exists")
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{request.agent_id}' already exists"
        )

    initial_content = _build_create_agent_initial_content(request)

    print(f"[DEBUG] Creating agent directory with content files: {list(initial_content.keys())}")
    print(f"[DEBUG] Environment name: {request.environment_name}")

    effective_environment_url = request.environment_url or settings.environment_url

    # Create agent directory structure with environment skill bootstrap
    create_result = await agent_fs.create_agent_directory(
        runtime_id=request.runtime_id or "openclaw",
        runtime_content_hash=request.runtime_content_hash,
        initial_content=initial_content if initial_content else None,
        agent_name=request.agent_name or request.agent_id,
        environment_url=effective_environment_url,
        environment_name=request.environment_name,
    )
    if not create_result.get("created"):
        detail = str(create_result.get("error") or "Failed to create agent directory")
        raise HTTPException(status_code=400, detail=detail)

    print(f"[SUCCESS] Agent directory created at {agent_fs.agent_path}")
    if create_result.get("environment_bootstrap"):
        bootstrap = create_result["environment_bootstrap"]
        if bootstrap.get("success"):
            print(f"[SUCCESS] Environment skill bootstrapped: {bootstrap.get('skill_path')}")
        else:
            print(f"[WARNING] Environment skill bootstrap issue: {bootstrap.get('error')}")

    return CreateAgentResponse(
        agent_id=request.agent_id,
        run_id=request.run_id or "default",
        created=True,
        path=str(agent_fs.agent_path),
        runtime_resolution=create_result.get("runtime_resolution"),
        environment_bootstrap=create_result.get("environment_bootstrap"),
        bootstrap_validation=create_result.get("bootstrap_validation"),
        render_warnings=create_result.get("render_warnings"),
    )


async def _create_agent_batch_result(request: CreateAgentRequest) -> CreateAgentBatchResult:
    """Convert create-agent exceptions into per-item batch results."""
    try:
        response = await _create_agent_from_request(request)
        return CreateAgentBatchResult(
            agent_id=response.agent_id,
            run_id=response.run_id,
            created=response.created,
            path=response.path,
            runtime_resolution=response.runtime_resolution,
            environment_bootstrap=response.environment_bootstrap,
            bootstrap_validation=response.bootstrap_validation,
            render_warnings=response.render_warnings,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return CreateAgentBatchResult(
            agent_id=request.agent_id,
            run_id=request.run_id or "default",
            created=False,
            error=detail,
        )
    except Exception as exc:
        return CreateAgentBatchResult(
            agent_id=request.agent_id,
            run_id=request.run_id or "default",
            created=False,
            error=str(exc),
        )


@router.post("/agents", response_model=CreateAgentResponse)
async def create_agent(request: CreateAgentRequest) -> CreateAgentResponse:
    """Create a new agent filesystem."""
    return await _create_agent_from_request(request)


@router.post("/agents/batch", response_model=CreateAgentsBatchResponse)
async def create_agents_batch(request: CreateAgentsBatchRequest) -> CreateAgentsBatchResponse:
    """Create many agent filesystems with bounded concurrency."""
    if not request.agents:
        raise HTTPException(status_code=400, detail="Batch request must contain at least one agent")

    seen_ids: set[tuple[str, str]] = set()
    duplicates: List[str] = []
    for agent_request in request.agents:
        run_id = str(agent_request.run_id or "default")
        key = (run_id, agent_request.agent_id)
        if key in seen_ids:
            duplicates.append(f"{run_id}:{agent_request.agent_id}")
            continue
        seen_ids.add(key)
    if duplicates:
        raise HTTPException(
            status_code=400,
            detail=f"Batch request contains duplicate agent ids: {', '.join(duplicates)}",
        )

    resolved_concurrency = max(
        1,
        min(int(request.concurrency or settings.max_parallel_agents or 10), 500),
    )
    semaphore = asyncio.Semaphore(resolved_concurrency)
    ordered_results: List[Optional[CreateAgentBatchResult]] = [None] * len(request.agents)

    async def _runner(index: int, agent_request: CreateAgentRequest) -> None:
        async with semaphore:
            ordered_results[index] = await _create_agent_batch_result(agent_request)

    await asyncio.gather(
        *[
            asyncio.create_task(_runner(index, agent_request))
            for index, agent_request in enumerate(request.agents)
        ]
    )

    results = [result for result in ordered_results if result is not None]
    created_count = sum(1 for result in results if result.created)
    return CreateAgentsBatchResponse(
        requested_count=len(request.agents),
        created_count=created_count,
        error_count=len(results) - created_count,
        results=results,
    )


@router.get("/agents/{agent_id}", response_model=AgentInfoResponse)
async def get_agent(agent_id: str, run_id: str = "default") -> AgentInfoResponse:
    """Get agent information."""
    agent_fs = AgentFilesystem(agent_id, run_id)
    info = await agent_fs.get_agent_info()
    
    if not info["exists"]:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )
    
    return AgentInfoResponse(**info)


@router.delete("/agents/{agent_id}", response_model=DeleteAgentResponse)
async def delete_agent(agent_id: str, run_id: str = "default") -> DeleteAgentResponse:
    """Remove an agent and all its data."""
    agent_fs = AgentFilesystem(agent_id, run_id)
    
    deleted = await agent_fs.delete_agent()
    
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )
    
    return DeleteAgentResponse(
        agent_id=agent_id,
        deleted=True
    )


@router.post("/agents/{agent_id}/skills/{skill_name}/install", response_model=InstallSkillResponse)
async def install_skill(
    agent_id: str,
    skill_name: str,
    request: InstallSkillRequest
) -> InstallSkillResponse:
    """Install a skill to an agent's filesystem.
    
    Per Skill Acquisition Contract (§3.2):
    - Fetches skill files from environment (SKILL.md, HEARTBEAT.md, MESSAGING.md)
    - Stores them in /agents/{agent_id}/skills/{skill_name}/
    - Skill content will be included in heartbeat prompt assembly
    """
    # Check if agent exists
    agent_fs = AgentFilesystem(agent_id)
    if not await agent_fs.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )
    
    # Use provided environment URL or fall back to settings
    environment_url = request.environment_url or settings.environment_url
    
    # Install the skill
    result = await agent_fs.install_skill(
        skill_name=skill_name,
        environment_url=environment_url or "",
        skill_files=request.skill_files
    )
    
    if not result["success"]:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to install skill '{skill_name}': {', '.join(result['errors'])}"
        )
    
    return InstallSkillResponse(
        agent_id=agent_id,
        skill_name=skill_name,
        success=result["success"],
        installed_files=result["installed_files"],
        errors=result["errors"],
        skill_path=result["skill_path"]
    )


@router.get("/agents/{agent_id}/skills", response_model=ListSkillsResponse)
async def list_skills(agent_id: str) -> ListSkillsResponse:
    """List all installed skills for an agent."""
    # Check if agent exists
    agent_fs = AgentFilesystem(agent_id)
    if not await agent_fs.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )
    
    skills = await agent_fs.get_installed_skills()
    
    return ListSkillsResponse(
        agent_id=agent_id,
        skills=skills,
        skills_path=str(agent_fs.skills_path)
    )


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "service": "agent-launcher"}
