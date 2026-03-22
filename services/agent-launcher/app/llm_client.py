"""LLM client for interacting with different providers."""

import json
import math
import re
from urllib.parse import urlparse

import httpx
from typing import Optional, Dict, Any, AsyncGenerator, List

from .config import settings


def _as_int(*values: Any) -> Optional[int]:
    """Best-effort integer parser for token counters."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _as_float(*values: Any) -> Optional[float]:
    """Best-effort float parser for costs."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _normalize_usage(usage: Any, provider: str) -> Dict[str, Any]:
    """Normalize provider usage payload into stable keys."""
    usage_dict = usage if isinstance(usage, dict) else {}

    prompt_tokens = _as_int(usage_dict.get("prompt_tokens"), usage_dict.get("input_tokens"))
    completion_tokens = _as_int(usage_dict.get("completion_tokens"), usage_dict.get("output_tokens"))
    total_tokens = _as_int(usage_dict.get("total_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    total_cost = _as_float(
        usage_dict.get("total_cost"),
        usage_dict.get("total_cost_usd"),
        usage_dict.get("cost"),
    )

    normalized = {
        "provider": provider,
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
        "input_tokens": prompt_tokens or 0,
        "output_tokens": completion_tokens or 0,
        "total_tokens": total_tokens or 0,
        "total_cost": total_cost or 0.0,
        "has_usage": any(
            value is not None
            for value in (prompt_tokens, completion_tokens, total_tokens, total_cost)
        ),
    }

    if usage_dict:
        normalized["raw"] = usage_dict

    return normalized


def _normalize_reasoning_payload(value: Any) -> Dict[str, Any]:
    """Normalize provider reasoning payload into a stable JSON-safe structure."""
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        return {"text": text} if text else {}
    if isinstance(value, list):
        return {"items": value}
    if isinstance(value, dict):
        normalized = dict(value)
        text = normalized.get("text")
        if isinstance(text, str):
            normalized["text"] = text.strip()
        return normalized
    return {"value": value}


def _extract_reasoning_from_choice(choice: Any, message: Any) -> Dict[str, Any]:
    """Extract provider reasoning payload from a chat-completions response choice."""
    if isinstance(message, dict):
        for key in ("reasoning", "reasoning_details", "thinking"):
            payload = _normalize_reasoning_payload(message.get(key))
            if payload:
                payload["source"] = f"message.{key}"
                return payload
    if isinstance(choice, dict):
        for key in ("reasoning", "reasoning_details", "thinking"):
            payload = _normalize_reasoning_payload(choice.get(key))
            if payload:
                payload["source"] = f"choice.{key}"
                return payload
    return {}


class LLMError(Exception):
    """Base exception for LLM client errors."""
    
    def __init__(self, message: str, error_code: Optional[int] = None, error_type: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.error_type = error_type
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for API response."""
        return {
            "message": self.message,
            "code": self.error_code,
            "type": self.error_type or "llm_error"
        }


class LLMClient:
    """Client for interacting with LLM providers."""
    
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = (provider or settings.llm_provider).lower()
        self.model = model or settings.llm_model
        self.temperature = temperature or settings.llm_temperature
        self.max_tokens = max_tokens or settings.llm_max_tokens
        
        # Set up provider-specific configuration
        if self.provider == "openrouter":
            self.api_key = api_key or settings.openrouter_api_key
            self.base_url = settings.openrouter_base_url
            self.default_model = "openai/gpt-5-mini"
            self.http_referer = settings.openrouter_http_referer
            self.x_title = settings.openrouter_x_title
        elif self.provider == "openai":
            self.api_key = api_key or settings.openai_api_key
            self.base_url = "https://api.openai.com/v1"
            self.default_model = "gpt-5-mini"
        elif self.provider == "anthropic":
            self.api_key = api_key or settings.anthropic_api_key
            self.base_url = "https://api.anthropic.com/v1"
            self.default_model = "claude-3-opus-20240229"
        elif self.provider == "dummy":
            self.api_key = api_key or "dummy"
            self.base_url = ""
            self.default_model = "dummy-model"
        else:
            raise LLMError(f"Unsupported provider: {self.provider}")
        
        if not self.api_key:
            raise LLMError(f"API key not configured for provider: {self.provider}")
        
        self.model = self.model or self.default_model
        self._last_usage: Dict[str, Any] = {}
        self._last_reasoning: Dict[str, Any] = {}

    def get_last_usage(self) -> Dict[str, Any]:
        """Return normalized usage metadata for the most recent completion."""
        return dict(self._last_usage)

    def get_last_reasoning(self) -> Dict[str, Any]:
        """Return provider reasoning metadata for the most recent completion."""
        return dict(self._last_reasoning)

    @staticmethod
    def _extract_reasoning_from_choice(choice: Any, message: Any) -> Dict[str, Any]:
        """Expose reasoning extraction for tests and provider adapters."""
        return _extract_reasoning_from_choice(choice, message)

    @staticmethod
    def _normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Validate and normalize messages array for provider requests."""
        if not isinstance(messages, list) or not messages:
            raise LLMError("messages must be a non-empty list", error_type="invalid_messages")

        normalized: List[Dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise LLMError("each message must be an object", error_type="invalid_messages")
            role = str(message.get("role") or "").strip().lower()
            if role not in {"system", "user", "assistant"}:
                raise LLMError(
                    f"invalid message role: {role or 'missing'}",
                    error_type="invalid_messages",
                )
            content = message.get("content")
            if content is None:
                content = ""
            if not isinstance(content, str):
                content = str(content)
            normalized.append({"role": role, "content": content})
        return normalized

    @staticmethod
    def _dummy_compass_answers(question_count: int = 46) -> Dict[str, int]:
        """Generate a valid neutral compass submission for deterministic dummy runs."""
        return {f"q{index:02d}": 0 for index in range(1, max(1, question_count) + 1)}

    async def chat_completion_messages(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> tuple[str, float]:
        """Send a chat completion request using a full messages array."""
        self._last_usage = {}
        self._last_reasoning = {}
        normalized_messages = self._normalize_messages(messages)

        if self.provider == "openrouter":
            return await self._openrouter_chat_completion_messages(
                normalized_messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        if self.provider == "openai":
            content = await self._openai_chat_completion_messages(
                normalized_messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            return content, 0.0
        if self.provider == "anthropic":
            content = await self._anthropic_chat_completion_messages(
                normalized_messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            return content, 0.0
        if self.provider == "dummy":
            content = await self._dummy_chat_completion_messages(normalized_messages, tools=tools)
            return content, 0.0

        raise LLMError(f"Unsupported provider: {self.provider}")
    
    async def chat_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> tuple[str, float]:
        """Backward-compatible wrapper around multi-message chat completion."""
        return await self.chat_completion_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )
    
    async def _dummy_chat_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Backward-compatible dummy completion."""
        return await self._dummy_chat_completion_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )

    async def _dummy_chat_completion_messages(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Return deterministic dummy responses for multi-round tests.

        Behavior is observation-driven (not only round-count driven):
        - first round emits a read (`GET`) action
        - second round emits a write (`POST`) action only when observation context is present
        - otherwise returns `HEARTBEAT_OK`
        """
        assistant_messages = [item for item in messages if item.get("role") == "assistant"]
        assistant_turns = len(assistant_messages)
        last_user_message = next(
            (item.get("content", "") for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        last_user_lower = str(last_user_message or "").lower()
        has_observation_context = any(
            marker in last_user_lower
            for marker in (
                "action results from your previous response",
                "status code:",
                "request:",
                "preview:",
                "failure",
                "success",
                "action_type",
                "action_key",
            )
        )
        mutation_already_emitted = any(
            marker in str(item.get("content", "")).replace(" ", "").lower()
            for item in assistant_messages
            for marker in (
                '"method":"post"',
                "upvote_post(",
                "downvote_post(",
                "create_comment(",
                "/compass/submit",
            )
        )
        is_compass_context = self._infer_dummy_compass_context(messages)
        tool_names = self._dummy_tool_names(tools)
        is_feed_vote_context = self._infer_dummy_feed_vote_context(tool_names)

        if assistant_turns <= 0:
            if is_feed_vote_context:
                return 'get_feed({"limit":10,"sort":"new"})'
            return (
                'request({"method":"GET","path":"/contract",'
                '"description":"Read environment contract before mutation"})'
            )
        if has_observation_context and not mutation_already_emitted:
            if is_compass_context:
                return (
                    'request({"method":"POST","path":"/compass/submit","body":'
                    + json.dumps(
                        {
                            "instrument_version": "political_compass_v1",
                            "answers": self._dummy_compass_answers(),
                        },
                        separators=(",", ":"),
                    )
                    + ',"description":"Submit compass answers after initial observation"})'
                )
            if is_feed_vote_context:
                post_id = self._infer_dummy_feed_post_id(messages)
                if post_id:
                    return f'upvote_post({{"post_id":"{post_id}"}})'
                return "heartbeat_ok({})"
            return (
                'request({"method":"POST","path":"/api/v1/posts/example/comments",'
                '"json":{"content":"Insightful follow-up from autonomous heartbeat."},'
                '"description":"Write a comment based on previous observations"})'
            )
        return "HEARTBEAT_OK"

    @staticmethod
    def _dummy_tool_names(tools: Optional[List[Dict[str, Any]]]) -> set[str]:
        names: set[str] = set()
        if not isinstance(tools, list):
            return names
        for item in tools:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if name:
                names.add(name)
        return names

    @staticmethod
    def _infer_dummy_feed_vote_context(tool_names: set[str]) -> bool:
        if not tool_names:
            return False
        return {"get_feed", "upvote_post", "downvote_post"}.issubset(tool_names) and "create_comment" not in tool_names

    @staticmethod
    def _infer_dummy_feed_post_id(messages: List[Dict[str, str]]) -> Optional[str]:
        preview_pattern = re.compile(r"Preview:\s*(\[.*\])", flags=re.IGNORECASE)
        id_pattern = re.compile(r'"id"\s*:\s*"([^"]+)"')

        for item in reversed(messages):
            if item.get("role") != "user":
                continue
            content = str(item.get("content", ""))
            preview_match = preview_pattern.search(content)
            if not preview_match:
                continue
            preview = preview_match.group(1)
            try:
                payload = json.loads(preview)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, list):
                for post in payload:
                    if isinstance(post, dict):
                        post_id = str(post.get("id") or "").strip()
                        upvotes = post.get("upvotes")
                        downvotes = post.get("downvotes")
                        should_pick = (
                            upvotes is None
                            and downvotes is None
                        ) or (int(upvotes or 0) <= 0 and int(downvotes or 0) <= 0)
                        if post_id and should_pick:
                            return post_id
                regex_match = id_pattern.search(preview)
            if regex_match:
                post_id = str(regex_match.group(1) or "").strip()
                if post_id:
                    return post_id
        return None

    @staticmethod
    def _infer_dummy_environment_base_url(messages: List[Dict[str, str]]) -> str:
        """Best-effort environment URL extraction for deterministic dummy runs."""
        joined = "\n".join(str(item.get("content", "")) for item in messages)
        prioritized_patterns = (
            r'"environment_url"\s*:\s*"(https?://[^"]+)"',
            r"environment_url[^h]*(https?://[^\s'\"<>)]+)",
        )
        for pattern in prioritized_patterns:
            match = re.search(pattern, joined, flags=re.IGNORECASE)
            if match:
                base_url = LLMClient._normalize_dummy_base_url(match.group(1))
                if base_url:
                    return base_url

        for raw_url in re.findall(r"https?://[^\s'\"<>)]+", joined, flags=re.IGNORECASE):
            base_url = LLMClient._normalize_dummy_base_url(raw_url)
            if base_url:
                return base_url

        return "http://environment.local"

    @staticmethod
    def _normalize_dummy_base_url(raw_url: str) -> Optional[str]:
        candidate = str(raw_url or "").strip().rstrip(".,;])")
        if not candidate:
            return None
        parsed = urlparse(candidate)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _infer_dummy_compass_context(messages: List[Dict[str, str]]) -> bool:
        joined = "\n".join(str(item.get("content", "")) for item in messages).lower()
        return any(
            marker in joined
            for marker in (
                "/compass/",
                "compass gate",
                "compass instrument",
                "instrument_version",
            )
        )
    
    async def _openai_chat_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Send chat completion request to OpenAI."""
        return await self._openai_chat_completion_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )

    async def _openai_chat_completion_messages(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> str:
        """Send chat completion request to OpenAI with messages array."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            
            if response.status_code != 200:
                raise LLMError(
                    f"OpenAI API error: {response.status_code} - {response.text}"
                )
            
            data = response.json()
            self._last_usage = _normalize_usage(data.get("usage"), provider="openai")
            choice = data["choices"][0]
            message = choice["message"]
            self._last_reasoning = self._extract_reasoning_from_choice(choice, message)
            return self._render_provider_message_content(message)
    
    async def _anthropic_chat_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Send chat completion request to Anthropic."""
        return await self._anthropic_chat_completion_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )

    async def _anthropic_chat_completion_messages(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> str:
        """Send chat completion request to Anthropic with messages array."""
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        system_parts = [item.get("content", "") for item in messages if item.get("role") == "system"]
        anthropic_messages = [
            {"role": item["role"], "content": item.get("content", "")}
            for item in messages
            if item.get("role") in {"user", "assistant"}
        ]
        if not anthropic_messages:
            anthropic_messages = [{"role": "user", "content": ""}]
        
        payload = {
            "model": self.model,
            "system": "\n\n".join(system_parts),
            "messages": anthropic_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "name": item.get("function", {}).get("name"),
                    "description": item.get("function", {}).get("description", ""),
                    "input_schema": item.get("function", {}).get("parameters", {"type": "object"}),
                }
                for item in tools
                if isinstance(item, dict) and isinstance(item.get("function"), dict)
            ]
            if tool_choice:
                payload["tool_choice"] = {"type": "auto"} if tool_choice == "auto" else {"type": tool_choice}
        
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers=headers,
                json=payload,
            )
            
            if response.status_code != 200:
                raise LLMError(
                    f"Anthropic API error: {response.status_code} - {response.text}"
                )
            
            data = response.json()
            self._last_usage = _normalize_usage(data.get("usage"), provider="anthropic")
            self._last_reasoning = {}
            content_blocks = data.get("content")
            if isinstance(content_blocks, list):
                tool_lines: List[str] = []
                text_lines: List[str] = []
                for block in content_blocks:
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "").strip().lower()
                    if block_type == "tool_use":
                        name = str(block.get("name") or "").strip()
                        args = block.get("input") if isinstance(block.get("input"), dict) else {}
                        if name:
                            tool_lines.append(
                                f"{name}({json.dumps(args, separators=(',', ':'), ensure_ascii=True)})"
                            )
                    elif block_type == "text":
                        text = block.get("text")
                        if isinstance(text, str) and text.strip():
                            text_lines.append(text)
                if tool_lines:
                    return "\n".join(tool_lines)
                if text_lines:
                    return "\n".join(text_lines)
            return ""
    
    async def _openrouter_chat_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> tuple[str, float]:
        """Send chat completion request to OpenRouter."""
        return await self._openrouter_chat_completion_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
        )

    async def _openrouter_chat_completion_messages(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> tuple[str, float]:
        """Send chat completion request to OpenRouter using messages array."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # Add optional OpenRouter-specific headers if configured
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.x_title:
            headers["X-Title"] = self.x_title
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            
            if response.status_code != 200:
                error_code = response.status_code
                error_type = "api_error"
                error_message = f"OpenRouter API error: {error_code}"
                
                # Try to parse error response for more details
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_detail = error_data["error"]
                        if isinstance(error_detail, dict):
                            error_message = error_detail.get("message", error_message)
                            error_code = error_detail.get("code", error_code)
                            # Map common OpenRouter error codes
                            if error_code == 402:
                                error_type = "insufficient_credits"
                                error_message = "Insufficient credits. Add more at openrouter.ai/settings/credits"
                            elif error_code == 401:
                                error_type = "authentication_error"
                            elif error_code == 429:
                                error_type = "rate_limit"
                        elif isinstance(error_detail, str):
                            error_message = error_detail
                except:
                    pass
                
                raise LLMError(error_message, error_code=error_code, error_type=error_type)
            
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            content = self._render_provider_message_content(message)
            usage = _normalize_usage(data.get("usage"), provider="openrouter")
            self._last_reasoning = self._extract_reasoning_from_choice(choice, message)
            
            # Extract cost from OpenRouter response
            cost = 0.0
            if "usage" in data and "total_cost" in data["usage"]:
                cost = data["usage"]["total_cost"]
            else:
                cost = float(usage.get("total_cost") or 0.0)

            # Keep derived cost synchronized in usage snapshot.
            if usage:
                usage["total_cost"] = float(cost)
            self._last_usage = usage
            
            return content, cost

    @staticmethod
    def _render_provider_message_content(message: Dict[str, Any]) -> str:
        """Normalize provider tool-call responses into direct-call text."""
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            rendered_lines: List[str] = []
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function_payload = tool_call.get("function")
                if not isinstance(function_payload, dict):
                    continue
                name = str(function_payload.get("name") or "").strip()
                if not name:
                    continue
                raw_arguments = function_payload.get("arguments")
                arguments_obj: Any = {}
                if isinstance(raw_arguments, str):
                    raw_text = raw_arguments.strip()
                    if raw_text:
                        try:
                            arguments_obj = json.loads(raw_text)
                        except Exception:
                            arguments_obj = {}
                elif isinstance(raw_arguments, dict):
                    arguments_obj = raw_arguments
                if not isinstance(arguments_obj, dict):
                    arguments_obj = {}
                rendered_lines.append(
                    f"{name}({json.dumps(arguments_obj, separators=(',', ':'), ensure_ascii=True)})"
                )
            if rendered_lines:
                return "\n".join(rendered_lines)

        content = message.get("content")
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_value = item.get("text")
                    if isinstance(text_value, str):
                        text_parts.append(text_value)
            return "\n".join(part for part in text_parts if part)
        return str(content)
    
    async def stream_chat_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion from the LLM."""
        if self.provider == "openrouter":
            async for chunk in self._openrouter_stream_completion(system_prompt, user_message):
                yield chunk
        elif self.provider == "openai":
            async for chunk in self._openai_stream_completion(system_prompt, user_message):
                yield chunk
        elif self.provider == "anthropic":
            async for chunk in self._anthropic_stream_completion(system_prompt, user_message):
                yield chunk
        else:
            raise LLMError(f"Unsupported provider: {self.provider}")
    
    async def _openai_stream_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion from OpenAI."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise LLMError(
                        f"OpenAI API error: {response.status_code}"
                    )
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data)
                            if chunk["choices"][0]["delta"].get("content"):
                                yield chunk["choices"][0]["delta"]["content"]
                        except (json.JSONDecodeError, KeyError):
                            continue
    
    async def _anthropic_stream_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion from Anthropic."""
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        
        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise LLMError(
                        f"Anthropic API error: {response.status_code}"
                    )
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        try:
                            import json
                            chunk = json.loads(data)
                            if chunk.get("type") == "content_block_delta":
                                yield chunk["delta"].get("text", "")
                        except json.JSONDecodeError:
                            continue
    
    async def _openrouter_stream_completion(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion from OpenRouter."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # Add optional OpenRouter-specific headers if configured
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.x_title:
            headers["X-Title"] = self.x_title
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise LLMError(
                        f"OpenRouter API error: {response.status_code}"
                    )
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data)
                            if chunk["choices"][0]["delta"].get("content"):
                                yield chunk["choices"][0]["delta"]["content"]
                        except (json.JSONDecodeError, KeyError):
                            continue


async def get_llm_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMClient:
    """Get an LLM client instance."""
    return LLMClient(provider=provider, model=model)
