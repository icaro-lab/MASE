"""Core OpenClaw-like heartbeat loop primitives."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..action_parser import Action, ActionType, parse_multiple_actions
from ..executor import ActionExecutor
from ..llm_client import LLMClient, LLMError
from ..simplified_social_core import (
    SIMPLIFIED_FEED_VOTE_LLM_TOOLS,
    SIMPLIFIED_SOCIAL_LLM_TOOLS,
)

PROMPT_CONTRACT_VERSION = "v1.1"
INTERACTION_MODE = "heartbeat_autonomous"
DEFAULT_HEARTBEAT_PROMPT = (
    "Read HEARTBEAT.md if it exists (workspace context). "
    "Follow it strictly. "
    "Do not infer or repeat old tasks from prior chats. "
    "If nothing needs attention, reply HEARTBEAT_OK."
)

OPENCLAW_CORE_LLM_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read file contents from the workspace.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create or overwrite a file in the workspace.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append",
            "description": "Append text to a file in the workspace.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace text in a workspace file.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "find": {"type": "string"},
                    "replace": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete",
            "description": "Delete a file from the workspace.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request",
            "description": "Send an HTTP request to the allowlisted environment.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "url": {"type": "string"},
                    "path": {"type": "string"},
                    "headers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "json": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "body": {"type": "string"},
                },
                "required": ["method"],
            },
        },
    },
]


def llm_tools_for_agent_core(agent_core: str) -> Optional[List[Dict[str, Any]]]:
    normalized = str(agent_core or "").strip().lower()
    if normalized == "openclaw_py_core":
        return OPENCLAW_CORE_LLM_TOOLS
    if normalized == "simplified_social_core":
        return SIMPLIFIED_SOCIAL_LLM_TOOLS
    if normalized == "openclaw_py_minimal_core":
        return SIMPLIFIED_FEED_VOTE_LLM_TOOLS
    return None


class StopReason:
    """Heartbeat turn stop reasons."""

    HEARTBEAT_OK = "heartbeat_ok"
    NO_ACTIONABLE_ACTIONS = "no_actionable_actions"
    MAX_MODEL_CALLS_REACHED = "max_model_calls_reached"
    MAX_ACTIONS_REACHED = "max_actions_reached"
    MAX_TICK_RUNTIME_REACHED = "max_tick_runtime_reached"
    GATED_SKIP = "gated_skip"
    ERROR = "error"


def _coerce_nonnegative_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _coerce_nonnegative_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "authorization", "api_key", "secret")):
                redacted[str(key)] = "***"
                continue
            redacted[str(key)] = _sanitize_json_value(nested)
        return redacted
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    return value


def _sanitize_preview_text(text: str) -> str:
    preview = str(text or "")
    preview = re.sub(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer ***", preview, flags=re.IGNORECASE)
    preview = re.sub(r"(?i)(api[_-]?key|token|authorization)\s*[:=]\s*[^\s,;]+", r"\1=***", preview)
    preview = re.sub(r'(?i)"(api[_-]?token|access[_-]?token|api[_-]?key|token|authorization|secret)"\s*:\s*"[^"]+"', r'"\1":"***"', preview)
    return preview


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return str(value)


def _summarize_response_preview(result: Dict[str, Any]) -> Optional[str]:
    action_name = str(result.get("action_name") or "")
    payload = result.get("response")
    if payload is None:
        payload = result.get("environment_response")
    if isinstance(payload, dict) and isinstance(payload.get("body"), (dict, list)):
        payload = payload.get("body")

    if action_name == "get_compass_instrument" and isinstance(payload, dict):
        instrument = payload.get("instrument") if isinstance(payload.get("instrument"), dict) else {}
        questions = instrument.get("questions") if isinstance(instrument.get("questions"), list) else []
        question_ids = [str(item.get("id") or "").strip() for item in questions if isinstance(item, dict)]
        summary = {
            "instrument_version": payload.get("instrument_version") or instrument.get("instrument_version"),
            "state": payload.get("state"),
            "question_count": len(question_ids),
            "question_ids": question_ids,
            "allowed_values": (((instrument.get("answer_scale") or {}).get("allowed_values")) if isinstance(instrument.get("answer_scale"), dict) else None),
        }
        return _compact_json(summary)

    if action_name == "get_feed" and isinstance(payload, list):
        summary = []
        for item in payload[:10]:
            if not isinstance(item, dict):
                continue
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            summary.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "snippet": str(item.get("content") or "")[:220],
                    "author": author.get("name"),
                    "upvotes": item.get("upvotes"),
                    "downvotes": item.get("downvotes"),
                    "score": item.get("score"),
                }
            )
        return _compact_json(summary)

    if action_name == "get_post" and isinstance(payload, dict):
        author = payload.get("author") if isinstance(payload.get("author"), dict) else {}
        summary = {
            "id": payload.get("id"),
            "title": payload.get("title"),
            "author": author.get("name"),
            "comment_count": payload.get("comment_count"),
            "score": payload.get("score"),
            "content_preview": str(payload.get("content") or "")[:280],
        }
        return _compact_json(summary)

    if action_name == "get_post_comments" and isinstance(payload, list):
        summary = []
        for item in payload[:10]:
            if not isinstance(item, dict):
                continue
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            summary.append(
                {
                    "id": item.get("id"),
                    "author": author.get("name"),
                    "parent_id": item.get("parent_id"),
                    "content_preview": str(item.get("content") or "")[:200],
                }
            )
        return _compact_json(summary)

    return None


def summarize_action(action: Action) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    try:
        if hasattr(action, "model_dump"):
            data = action.model_dump()
        elif hasattr(action, "dict"):
            data = action.dict()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    keep = ("action", "action_name", "description", "method", "url", "operation", "path")
    summary: Dict[str, Any] = {}
    for key in keep:
        value = data.get(key)
        if value is None or value == "":
            continue
        summary[key] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return summary


def normalize_observations(
    results: List[Dict[str, Any]],
    *,
    max_chars: int,
    sanitize: bool = True,
) -> List[Dict[str, Any]]:
    bundles: List[Dict[str, Any]] = []
    limit = max(int(max_chars or 2000), 1)

    for index, raw in enumerate(results):
        result = raw if isinstance(raw, dict) else {"raw": str(raw)}
        action_type = str(result.get("action_type") or result.get("action") or "unknown")
        bundle: Dict[str, Any] = {
            "index": index,
            "action_key": str(result.get("action_key") or f"{action_type}:{index}"),
            "action_name": str(result.get("action_name") or action_type),
            "action_type": action_type,
            "success": bool(result.get("success")),
        }
        status_code = result.get("status_code")
        if status_code is not None:
            bundle["status_code"] = _coerce_nonnegative_int(status_code, 0)
        if result.get("method"):
            bundle["method"] = str(result.get("method"))
        if result.get("path"):
            bundle["path"] = str(result.get("path"))
        if result.get("operation"):
            bundle["operation"] = str(result.get("operation"))
        if result.get("url"):
            bundle["url"] = str(result.get("url"))
        if result.get("error_code"):
            bundle["error_code"] = str(result.get("error_code"))
        if result.get("error"):
            bundle["error"] = (
                _sanitize_preview_text(str(result.get("error")))
                if sanitize
                else str(result.get("error"))
            )

        response_preview = ""
        summarized_preview = _summarize_response_preview(result)
        if summarized_preview is not None:
            response_preview = summarized_preview
        elif result.get("response") is not None:
            payload = _sanitize_json_value(result.get("response")) if sanitize else result.get("response")
            response_preview = _compact_json(payload)
        elif result.get("environment_response") is not None:
            payload = (
                _sanitize_json_value(result.get("environment_response"))
                if sanitize
                else result.get("environment_response")
            )
            response_preview = _compact_json(payload)
        elif result.get("content") is not None:
            response_preview = str(result.get("content"))

        if response_preview:
            if sanitize:
                response_preview = _sanitize_preview_text(response_preview)
            if len(response_preview) > limit:
                bundle["response_preview"] = response_preview[:limit]
                bundle["response_preview_truncated"] = True
            else:
                bundle["response_preview"] = response_preview

        bundles.append(bundle)

    return bundles


def format_observation_message(observations: List[Dict[str, Any]]) -> str:
    if not observations:
        return (
            "No new tool results were produced in the previous round. "
            "Continue from the current runtime state only, do not invent new objects or unsupported actions, "
            "and use heartbeat_ok({}) if nothing needs attention."
        )

    lines = [
        "Action results from your previous response:",
        "",
    ]
    for idx, item in enumerate(observations, start=1):
        descriptor = f"{item.get('action_type', 'unknown')}:{item.get('action_name', 'unnamed')}"
        status = "success" if item.get("success") else "failure"
        lines.append(f"{idx}. {descriptor} ({status})")
        if item.get("method") and item.get("path"):
            lines.append(f"   Request: {item['method']} {item['path']}")
        if item.get("status_code") is not None:
            lines.append(f"   Status code: {item['status_code']}")
        if item.get("error"):
            lines.append(f"   Error: {item['error']}")
        if item.get("response_preview"):
            lines.append(f"   Preview: {item['response_preview']}")
    return "\n".join(lines)


@dataclass
class TurnResult:
    """Outcome of a heartbeat turn execution."""

    status: str
    stop_reason: str
    actions_executed: int
    results: List[Dict[str, Any]]
    summary: str
    llm_cost: float
    rounds_executed: int
    model_calls: int
    elapsed_ms: int
    round_details: List[Dict[str, Any]]
    transcript_messages: List[Dict[str, str]]
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    error: Optional[Dict[str, Any]] = None


RoundZeroFallback = Callable[[str, Optional[str], Optional[str]], Awaitable[Dict[str, Any]]]


class TurnOrchestrator:
    """Execute bounded multi-call heartbeat turns."""

    def __init__(
        self,
        *,
        agent_id: str,
        run_id: str,
        tick: Optional[int],
        policy: Any,
        system_prompt: str,
        user_prompt: str,
        llm_client: LLMClient,
        executor: ActionExecutor,
        initial_messages: Optional[List[Dict[str, str]]] = None,
        memory_message: Optional[str] = None,
        memory_mode: str = "stateless",
        memory_turns_loaded: int = 0,
        round_zero_fallback: Optional[RoundZeroFallback] = None,
    ):
        self.agent_id = agent_id
        self.run_id = run_id
        self.tick = tick
        self.policy = policy
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.llm_client = llm_client
        self.executor = executor
        self.initial_messages = list(initial_messages or [])
        self.memory_message = memory_message
        self.memory_mode = memory_mode
        self.memory_turns_loaded = memory_turns_loaded
        self.round_zero_fallback = round_zero_fallback

    async def run(self) -> TurnResult:
        started_at = time.perf_counter()
        messages: List[Dict[str, str]] = [{"role": "system", "content": self.system_prompt}]
        if self.initial_messages:
            messages.extend(self.initial_messages)
        if self.memory_message:
            messages.append({"role": "user", "content": self.memory_message})
        messages.append({"role": "user", "content": self.user_prompt})

        aggregated_results: List[Dict[str, Any]] = []
        round_details: List[Dict[str, Any]] = []

        total_llm_cost = 0.0
        total_input_tokens = 0
        total_output_tokens = 0
        total_actions = 0
        max_model_calls = getattr(self.policy, "max_model_calls_per_tick", None)
        max_actions_per_round = getattr(self.policy, "max_actions_per_round", None)
        max_actions_per_tick = getattr(self.policy, "max_actions_per_tick", None)
        max_tick_runtime_ms = getattr(self.policy, "max_tick_runtime_ms", None)

        status = "completed"
        stop_reason = StopReason.NO_ACTIONABLE_ACTIONS
        summary = "Heartbeat turn completed"
        error_payload: Optional[Dict[str, Any]] = None
        llm_tools = llm_tools_for_agent_core(getattr(self.policy, "agent_core", ""))

        round_index = 0
        while True:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            if max_tick_runtime_ms is not None and elapsed_ms >= max_tick_runtime_ms:
                stop_reason = StopReason.MAX_TICK_RUNTIME_REACHED
                summary = "Heartbeat stopped: max tick runtime reached"
                break

            round_record: Dict[str, Any] = {
                "round_index": round_index,
                "prompt_contract_version": PROMPT_CONTRACT_VERSION,
                "interaction_mode": INTERACTION_MODE,
                "memory_mode": self.memory_mode,
                "memory_turns_loaded": self.memory_turns_loaded,
                "agent_core": getattr(self.policy, "agent_core", None),
                "message_count": len(messages),
            }

            try:
                llm_response, round_cost = await self.llm_client.chat_completion_messages(
                    messages,
                    tools=llm_tools,
                    tool_choice="auto" if llm_tools else None,
                )
            except LLMError as llm_error:
                if round_index == 0 and self.round_zero_fallback is not None:
                    fallback = await self.round_zero_fallback("llm_error", None, llm_error.message)
                    fallback_results = list(fallback.get("results") or [])
                    aggregated_results.extend(fallback_results)
                    total_actions += len(fallback_results)
                    status = str(fallback.get("status") or "error")
                    stop_reason = str(fallback.get("stop_reason") or StopReason.ERROR)
                    summary = str(fallback.get("summary") or "Heartbeat fallback executed after LLM error")
                    error_payload = llm_error.to_dict()
                    round_record.update(
                        {
                            "llm_error": llm_error.to_dict(),
                            "actions_executed_this_round": len(fallback_results),
                            "actions_executed_total": total_actions,
                            "stop_reason": stop_reason,
                        }
                    )
                    round_details.append(round_record)
                    break
                status = "error"
                stop_reason = StopReason.ERROR
                summary = f"Heartbeat failed: {llm_error.message}"
                error_payload = llm_error.to_dict()
                round_record.update({"llm_error": llm_error.to_dict(), "stop_reason": stop_reason})
                round_details.append(round_record)
                break
            except Exception as exc:
                status = "error"
                stop_reason = StopReason.ERROR
                summary = f"Heartbeat failed: {exc}"
                error_payload = {"message": str(exc), "type": "runtime_error"}
                round_record.update({"llm_error": error_payload, "stop_reason": stop_reason})
                round_details.append(round_record)
                break

            llm_usage = self.llm_client.get_last_usage() if hasattr(self.llm_client, "get_last_usage") else {}
            llm_reasoning = (
                self.llm_client.get_last_reasoning()
                if hasattr(self.llm_client, "get_last_reasoning")
                else {}
            )
            tokens_input = _coerce_nonnegative_int(
                llm_usage.get("prompt_tokens", llm_usage.get("input_tokens")),
                0,
            )
            tokens_output = _coerce_nonnegative_int(
                llm_usage.get("completion_tokens", llm_usage.get("output_tokens")),
                0,
            )
            normalized_cost = _coerce_nonnegative_float(
                round_cost,
                _coerce_nonnegative_float(llm_usage.get("total_cost"), 0.0),
            )
            total_llm_cost += normalized_cost
            total_input_tokens += tokens_input
            total_output_tokens += tokens_output

            try:
                parsed_actions = parse_multiple_actions(llm_response)
            except Exception as parse_error:
                parsed_actions = []
                parse_failure = str(parse_error)
            else:
                parse_failure = None

            actionable_actions = [action for action in parsed_actions if action.action != ActionType.HEARTBEAT_OK]
            has_heartbeat_ok = any(action.action == ActionType.HEARTBEAT_OK for action in parsed_actions)

            round_record.update(
                {
                    "response_sha256": hashlib.sha256(llm_response.encode("utf-8")).hexdigest(),
                    "response_text": _sanitize_preview_text(llm_response),
                    "parsed_action_count": len(parsed_actions),
                    "actionable_action_count": len(actionable_actions),
                    "actions": [summarize_action(action) for action in actionable_actions],
                    "llm_cost_usd": normalized_cost,
                    "llm_tokens_input": tokens_input,
                    "llm_tokens_output": tokens_output,
                    "usage": llm_usage,
                    "reasoning": llm_reasoning,
                }
            )

            if parse_failure:
                if round_index == 0 and self.round_zero_fallback is not None:
                    fallback = await self.round_zero_fallback("parse_error", llm_response, parse_failure)
                    fallback_results = list(fallback.get("results") or [])
                    aggregated_results.extend(fallback_results)
                    total_actions += len(fallback_results)
                    status = str(fallback.get("status") or "error")
                    stop_reason = str(fallback.get("stop_reason") or StopReason.ERROR)
                    summary = str(fallback.get("summary") or "Fallback executed after parse error")
                    round_record.update(
                        {
                            "parse_error": parse_failure,
                            "actions_executed_this_round": len(fallback_results),
                            "actions_executed_total": total_actions,
                            "stop_reason": stop_reason,
                        }
                    )
                    round_details.append(round_record)
                    break

                stop_reason = StopReason.NO_ACTIONABLE_ACTIONS
                summary = f"Heartbeat ended: unparseable model output in round {round_index} ({parse_failure})"
                round_record.update(
                    {
                        "parse_error": parse_failure,
                        "actions_executed_this_round": 0,
                        "actions_executed_total": total_actions,
                        "stop_reason": stop_reason,
                    }
                )
                round_details.append(round_record)
                break

            if not actionable_actions:
                if has_heartbeat_ok:
                    stop_reason = StopReason.HEARTBEAT_OK
                    summary = "Heartbeat acknowledged with HEARTBEAT_OK"
                elif round_index == 0 and self.round_zero_fallback is not None:
                    fallback = await self.round_zero_fallback("no_actionable_actions", llm_response, None)
                    fallback_results = list(fallback.get("results") or [])
                    aggregated_results.extend(fallback_results)
                    total_actions += len(fallback_results)
                    status = str(fallback.get("status") or "completed")
                    stop_reason = str(fallback.get("stop_reason") or StopReason.NO_ACTIONABLE_ACTIONS)
                    summary = str(
                        fallback.get("summary")
                        or "Heartbeat fallback executed because model returned no actionable actions"
                    )
                else:
                    stop_reason = StopReason.NO_ACTIONABLE_ACTIONS
                    summary = "Heartbeat ended: no actionable actions returned by model"

                round_record.update(
                    {
                        "actions_executed_this_round": 0,
                        "actions_executed_total": total_actions,
                        "stop_reason": stop_reason,
                    }
                )
                round_details.append(round_record)
                break

            if max_actions_per_tick is not None:
                remaining_actions = max(max_actions_per_tick - total_actions, 0)
            else:
                remaining_actions = len(actionable_actions)
            if max_actions_per_tick is not None and remaining_actions <= 0:
                stop_reason = StopReason.MAX_ACTIONS_REACHED
                summary = "Heartbeat stopped: max actions per tick reached"
                round_record.update(
                    {
                        "actions_executed_this_round": 0,
                        "actions_executed_total": total_actions,
                        "stop_reason": stop_reason,
                    }
                )
                round_details.append(round_record)
                break

            if max_actions_per_round is not None:
                per_round_cap = min(max_actions_per_round, remaining_actions)
            else:
                per_round_cap = remaining_actions
            round_results = await self.executor.execute_actions(actionable_actions, max_actions=per_round_cap)
            aggregated_results.extend(round_results)
            executed_this_round = len(round_results)
            total_actions += executed_this_round

            observations_for_model = normalize_observations(
                round_results,
                max_chars=self.policy.max_observation_chars_per_round,
                sanitize=False,
            )
            observations = normalize_observations(
                round_results,
                max_chars=self.policy.max_observation_chars_per_round,
            )
            observation_message = format_observation_message(observations_for_model)
            redacted_observation_message = format_observation_message(observations)

            messages.append({"role": "assistant", "content": llm_response})
            messages.append({"role": "user", "content": observation_message})

            round_record.update(
                {
                    "actions_executed_this_round": executed_this_round,
                    "actions_executed_total": total_actions,
                    "observations": observations,
                    "observation_message": redacted_observation_message,
                }
            )

            elapsed_after_round = int((time.perf_counter() - started_at) * 1000)
            if max_tick_runtime_ms is not None and elapsed_after_round >= max_tick_runtime_ms:
                stop_reason = StopReason.MAX_TICK_RUNTIME_REACHED
                summary = "Heartbeat stopped: max tick runtime reached"
                round_record["stop_reason"] = stop_reason
                round_details.append(round_record)
                break

            if max_actions_per_tick is not None and total_actions >= max_actions_per_tick:
                stop_reason = StopReason.MAX_ACTIONS_REACHED
                summary = "Heartbeat stopped: max actions per tick reached"
                round_record["stop_reason"] = stop_reason
                round_details.append(round_record)
                break

            round_index += 1
            if max_model_calls is not None and round_index >= max_model_calls:
                stop_reason = StopReason.MAX_MODEL_CALLS_REACHED
                summary = "Heartbeat stopped: max model calls per tick reached"
                round_record["stop_reason"] = stop_reason
                round_details.append(round_record)
                break

            round_record["stop_reason"] = "continue"
            round_details.append(round_record)

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        rounds_executed = len(round_details)

        return TurnResult(
            status=status,
            stop_reason=stop_reason,
            actions_executed=total_actions,
            results=aggregated_results,
            summary=summary,
            llm_cost=total_llm_cost,
            rounds_executed=rounds_executed,
            model_calls=rounds_executed,
            elapsed_ms=elapsed_ms,
            round_details=round_details,
            transcript_messages=messages[1:],
            total_tokens_input=total_input_tokens,
            total_tokens_output=total_output_tokens,
            error=error_payload,
        )
