"""Action executor for performing agent actions."""

import json
import httpx
import time
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from pydantic import BaseModel
from .action_parser import Action, ActionType, HTTPMethod
from .agent_fs import AgentFilesystem
from .config import settings
from .telemetry_client import ActionCategory, record_action
from .scheduler import heartbeat_scheduler


class ExecutionResult(BaseModel):
    """Result of executing an action."""
    success: bool
    action_type: str
    details: Dict[str, Any]
    timestamp: str
    error: Optional[str] = None


class ActionExecutor:
    """Executes agent actions against the active environment contract."""
    
    def __init__(
        self,
        agent_fs: AgentFilesystem,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        heartbeat_tick: Optional[int] = None,
        heartbeat_index: Optional[int] = None,
        agent_token: Optional[str] = None,
        environment_name: Optional[str] = None,
        primary_environment_url: Optional[str] = None,
        allowed_environment_urls: Optional[List[str]] = None,
        environment_auth_registry_seed: Optional[Dict[str, Any]] = None,
        allowed_environment_hosts: Optional[List[str]] = None,
    ):
        self.agent_fs = agent_fs
        self.agent_id = agent_id or "unknown"
        self.run_id = run_id or settings.run_id
        self.heartbeat_tick = heartbeat_tick
        self.heartbeat_index = heartbeat_index
        self.environment_name = str(environment_name or "").strip() or None
        self.primary_environment_url = (
            str(primary_environment_url or settings.environment_url or "").strip() or None
        )
        self.allowed_environment_hosts = self._build_allowed_environment_hosts(
            self.primary_environment_url,
            allowed_environment_urls,
            legacy_allowed_hosts=allowed_environment_hosts,
        )
        self.primary_environment_hosts = set(self._host_variants(self.primary_environment_url))
        self.environment_auth_registry: Dict[str, str] = {}
        self._seed_environment_auth_registry(environment_auth_registry_seed)
        if agent_token:
            self._register_host_token(
                self.primary_environment_url or settings.environment_url,
                agent_token,
                source="scheduler_primary",
            )
        self.http_client = httpx.AsyncClient(timeout=settings.http_timeout)
    
    async def close(self):
        """Close the HTTP client."""
        await self.http_client.aclose()
    
    async def execute_actions(
        self,
        actions: List[Action],
        max_actions: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Execute a list of actions and return results."""
        if max_actions is None:
            max_actions = len(actions)
        results = []

        if heartbeat_scheduler.is_paused():
            return results
        
        for i, action in enumerate(actions):
            if heartbeat_scheduler.is_paused():
                break

            if i >= max_actions:
                action_name = self._resolve_action_name(action)
                results.append({
                    "action": action.action,
                    "success": False,
                    "action_type": self._resolve_action_type(action),
                    "action_name": action_name,
                    "action_key": self._resolve_action_key(action, action_name),
                    "error": "Max actions limit reached",
                    "error_code": "max_actions_limit_reached",
                    "timestamp": datetime.now().isoformat(),
                })
                break
            
            started_at = time.perf_counter()
            result = await self.execute_action(action)
            result["action_type"] = result.get("action_type") or self._resolve_action_type(action)
            result["action_name"] = result.get("action_name") or self._resolve_action_name(action)
            result["action_key"] = result.get("action_key") or self._resolve_action_key(
                action,
                result["action_name"],
            )
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            result["duration_ms"] = duration_ms
            results.append(result)
            
            # Log the action execution
            await self._log_action_result(action, result)
            await self._record_action_telemetry(action, result)
        
        return results
    
    async def execute_action(self, action: Action) -> Dict[str, Any]:
        """Execute a single action."""
        timestamp = datetime.now().isoformat()
        
        try:
            if action.action == ActionType.HEARTBEAT_OK:
                return {
                    "action": "heartbeat_ok",
                    "success": True,
                    "action_type": "heartbeat_ok",
                    "action_name": "heartbeat_ok",
                    "error_code": None,
                    "message": "Heartbeat acknowledged",
                    "timestamp": timestamp,
                }
            
            elif action.action == ActionType.HTTP:
                return await self._execute_http(action)
            
            elif action.action == ActionType.FILESYSTEM:
                return await self._execute_filesystem(action)
            
            elif action.action == ActionType.UNKNOWN:
                return {
                    "action": "unknown",
                    "success": False,
                    "action_type": "unknown",
                    "action_name": "unknown_action",
                    "error": action.description or "Unknown action type",
                    "error_code": "unknown_action_type",
                    "timestamp": timestamp,
                }
            
            else:
                return {
                    "action": str(action.action),
                    "success": False,
                    "action_type": "unknown",
                    "action_name": str(action.action),
                    "error": f"Unhandled action type: {action.action}",
                    "error_code": "unhandled_action_type",
                    "timestamp": timestamp,
                }
        
        except Exception as e:
            return {
                "action": str(action.action),
                "success": False,
                "action_type": self._resolve_action_type(action),
                "action_name": self._resolve_action_name(action),
                "error": str(e),
                "error_code": "action_execution_exception",
                "timestamp": timestamp,
            }
    
    def _is_mutating_method(self, method: HTTPMethod) -> bool:
        """Check if HTTP method is mutating (requires IA evaluation)."""
        return method in (HTTPMethod.POST, HTTPMethod.PUT, HTTPMethod.DELETE, HTTPMethod.PATCH)

    @staticmethod
    def _has_header(headers: Dict[str, str], name: str) -> bool:
        """Check if headers contain a name, case-insensitive."""
        target = name.lower()
        return any(key.lower() == target for key in headers.keys())

    @staticmethod
    def _remove_header(headers: Dict[str, str], name: str) -> None:
        """Remove header case-insensitively."""
        target = name.lower()
        for key in list(headers.keys()):
            if key.lower() == target:
                headers.pop(key, None)

    @staticmethod
    def _is_registration_endpoint(url: Optional[str]) -> bool:
        """Return True when URL targets /auth/register endpoint."""
        if not url:
            return False
        try:
            parsed = urlparse(url)
            path = str(parsed.path or "").rstrip("/")
            return path.endswith("/auth/register")
        except Exception:
            return False

    @staticmethod
    def _extract_api_token(payload: Any) -> Optional[str]:
        """Extract API token from registration-like response payload."""
        if not isinstance(payload, dict):
            return None
        for field in ("api_token", "token", "access_token"):
            raw = payload.get(field)
            if raw is not None and str(raw).strip():
                return str(raw).strip()
        return None

    def _seed_environment_auth_registry(self, seed: Optional[Dict[str, Any]]) -> None:
        """Seed host-scoped auth registry from scheduler payload."""
        if not isinstance(seed, dict):
            return
        entries = seed.get("entries") if isinstance(seed.get("entries"), dict) else seed
        if not isinstance(entries, dict):
            return
        for host_key, raw_entry in entries.items():
            token: Optional[str] = None
            source = "seed"
            if isinstance(raw_entry, str):
                token = raw_entry.strip() or None
            elif isinstance(raw_entry, dict):
                token = self._extract_api_token(raw_entry)
                source = str(raw_entry.get("source") or source)
            if token:
                self._register_host_token(str(host_key), token, source=source)

    def _register_host_token(
        self,
        host_or_url: Optional[str],
        api_token: Optional[str],
        *,
        source: str,
    ) -> bool:
        """Persist auth token for all known variants of a host/url."""
        token = str(api_token or "").strip()
        if not token:
            return False
        variants = self._host_variants(host_or_url)
        if not variants:
            return False
        for host in variants:
            self.environment_auth_registry[host] = token
        print(
            "[INFO] auth_registry_updated "
            f"run_id={self.run_id or 'unknown'} agent_id={self.agent_id or 'unknown'} "
            f"host={variants[0]} source={source}"
        )
        return True

    def _resolve_host_token(self, target_url: Optional[str]) -> Optional[Dict[str, str]]:
        """Resolve host-scoped token for target URL."""
        for host in self._host_variants(target_url):
            token = self.environment_auth_registry.get(host)
            if token:
                return {"host": host, "token": token}
        return None

    async def _persist_registration_credentials(self, payload: Any) -> bool:
        """Persist registration credentials into workspace for later heartbeats."""
        if not self.environment_name:
            return False
        if not isinstance(payload, dict):
            return False
        api_token = self._extract_api_token(payload)
        if not api_token:
            return False
        agent_id = str(payload.get("agent_id") or self.agent_id or "").strip() or self.agent_id
        agent_name = str(
            payload.get("name") or payload.get("agent_name") or self.agent_id or "agent"
        ).strip()
        record = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "api_token": api_token,
        }
        try:
            await self.agent_fs.write_file(
                f".{self.environment_name}-credentials.json",
                json.dumps(record, ensure_ascii=True, indent=2),
            )
            return True
        except Exception as exc:
            print(
                "[WARNING] auth_registry_persist_failed "
                f"run_id={self.run_id or 'unknown'} agent_id={self.agent_id or 'unknown'} "
                f"environment={self.environment_name} error={exc}"
            )
            return False

    def _inject_runtime_headers(
        self,
        headers: Dict[str, str],
        *,
        target_url: Optional[str],
    ) -> Dict[str, str]:
        """Inject run/agent headers and host-scoped auth header.

        Preserve agent-supplied Authorization for allowlisted hosts so auth can
        persist across heartbeats via normal workspace state.
        """
        enriched_headers = dict(headers)

        has_authorization = self._has_header(enriched_headers, "Authorization")
        token_entry = self._resolve_host_token(target_url)
        if not has_authorization and token_entry:
            enriched_headers["Authorization"] = f"Bearer {token_entry['token']}"
        if self.run_id and not self._has_header(enriched_headers, "x-run-id"):
            enriched_headers["x-run-id"] = self.run_id
        if self.agent_id and self.agent_id != "unknown" and not self._has_header(enriched_headers, "x-agent-id"):
            enriched_headers["x-agent-id"] = self.agent_id

        return enriched_headers

    def _normalize_http_url(self, url: Optional[str]) -> Optional[str]:
        """Normalize HTTP URLs.

        IMPORTANT: keep environment-agnostic. No env-specific host rewriting.
        """
        value = str(url or "").strip()
        if not value:
            return None

        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            return value

        base_url = str(self.primary_environment_url or settings.environment_url or "").strip()
        if not base_url:
            return value

        normalized_base = base_url.rstrip("/") + "/"
        if value.startswith("/"):
            return urljoin(normalized_base, value.lstrip("/"))
        return urljoin(normalized_base, value)

    @staticmethod
    def _host_variants(raw_value: Optional[str]) -> List[str]:
        """Build normalized host variants from URL/host input."""
        value = (raw_value or "").strip()
        if not value:
            return []

        parsed_input = value if "://" in value else f"http://{value}"
        parsed = urlparse(parsed_input)
        variants: List[str] = []
        hostname = (parsed.hostname or "").lower()

        if parsed.netloc:
            variants.append(parsed.netloc.lower())
        if hostname:
            variants.append(hostname)
        elif value:
            variants.append(value.lower())
        if hostname and parsed.port:
            variants.append(f"{hostname}:{parsed.port}")

        unique: List[str] = []
        for item in variants:
            if item and item not in unique:
                unique.append(item)
        return unique

    def _resolve_runtime_placeholders(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._resolve_runtime_placeholders(nested) for key, nested in value.items()}
        if isinstance(value, list):
            return [self._resolve_runtime_placeholders(item) for item in value]
        if isinstance(value, str):
            if value == "__SELF_AGENT_ID__":
                return self.agent_id
        return value

    def _build_allowed_environment_hosts(
        self,
        primary_environment_url: Optional[str],
        additional_urls: Optional[List[str]],
        *,
        legacy_allowed_hosts: Optional[List[str]] = None,
    ) -> Set[str]:
        """Build allowlisted hosts for agent egress."""
        allowed: Set[str] = set()
        candidates: List[Optional[str]] = [primary_environment_url, settings.environment_url]

        if additional_urls:
            candidates.extend(additional_urls)
        if legacy_allowed_hosts:
            candidates.extend(legacy_allowed_hosts)

        for candidate in candidates:
            for variant in self._host_variants(candidate):
                allowed.add(variant)

        return allowed

    def _is_url_allowed(self, url: Optional[str]) -> bool:
        """Return True if URL host belongs to allowed environment hosts."""
        if not url:
            return False
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        netloc = parsed.netloc.lower()
        hostname = (parsed.hostname or "").lower()
        allowlisted = self.allowed_environment_hosts

        allowlisted_netlocs = {entry for entry in allowlisted if ":" in entry}
        allowlisted_hostnames = {entry.split(":")[0] for entry in allowlisted_netlocs}
        allowlisted_hostnames |= {entry for entry in allowlisted if ":" not in entry}

        if allowlisted_netlocs:
            if netloc in allowlisted_netlocs:
                return True
            if hostname and hostname in allowlisted_hostnames and ":" not in netloc:
                return True
            return False

        return hostname in allowlisted_hostnames or netloc in allowlisted

    def _resolve_action_name(self, action: Action) -> str:
        """Resolve action name for logs and telemetry."""
        if action.action_name:
            return action.action_name
        if action.description:
            return action.description
        if action.action == ActionType.HTTP:
            method = (action.method or HTTPMethod.GET).value.lower()
            return f"http_{method}"
        if action.action == ActionType.FILESYSTEM:
            operation = (action.operation or "write").lower()
            return f"fs_{operation}"
        return str(action.action.value if isinstance(action.action, ActionType) else action.action)

    def _resolve_action_type(self, action: Action) -> str:
        """Resolve normalized action type for telemetry."""
        if action.action == ActionType.HTTP:
            method = (action.method or HTTPMethod.GET).value.lower()
            return f"http_{method}"
        if action.action == ActionType.FILESYSTEM:
            operation = (action.operation or "write").lower()
            return f"fs_{operation}"
        if action.action == ActionType.HEARTBEAT_OK:
            return "heartbeat_ok"
        return "unknown"

    @staticmethod
    def _is_skill_state_path(target_path: Path, skills_root: Path) -> bool:
        """Return True for mutable state under an installed skill bundle.

        Skill docs themselves should stay reference material. The only writable
        area inside `skills/` is a dedicated per-skill state/memory subtree.
        """
        try:
            relative = target_path.relative_to(skills_root)
        except ValueError:
            return False

        parts = relative.parts
        if len(parts) < 3:
            return False
        return parts[1] in {"state", "memory", ".state", ".memory"}

    def _resolve_action_key(self, action: Action, action_name: Optional[str] = None) -> str:
        """Resolve stable action key for result/telemetry correlation."""
        resolved_name = action_name or self._resolve_action_name(action)
        if action.action == ActionType.HTTP:
            method = (action.method or HTTPMethod.GET).value.upper()
            path = action.url or "unknown"
            return f"{method}:{path}:{resolved_name}"
        if action.action == ActionType.FILESYSTEM:
            operation = (action.operation or "write").lower()
            path = action.path or "unknown"
            return f"FS:{operation}:{path}:{resolved_name}"
        if action.action == ActionType.HEARTBEAT_OK:
            return "SYSTEM:heartbeat_ok"
        return f"{self._resolve_action_type(action)}:{resolved_name}"

    def _resolve_action_category(self, action: Action) -> str:
        """Resolve telemetry action category."""
        if action.action == ActionType.HTTP:
            return ActionCategory.ENVIRONMENTAL.value
        if action.action == ActionType.FILESYSTEM:
            return ActionCategory.SELF.value
        return ActionCategory.SYSTEM.value

    @staticmethod
    def _extract_request_id(headers: Dict[str, Any]) -> Optional[str]:
        """Extract request identifier from response headers."""
        request_id_headers = (
            "x-request-id",
            "request-id",
            "x-correlation-id",
            "x-trace-id",
        )
        for key, value in headers.items():
            if key.lower() in request_id_headers:
                return str(value)
        return None

    def _log_dispatch_failure(
        self,
        *,
        action_type: str,
        action_name: str,
        method: Optional[str],
        path: Optional[str],
        status_code: Optional[int],
        request_id: Optional[str],
        error_code: str,
        error: str,
    ) -> None:
        """Emit structured dispatch failure log with actionable fields."""
        print(
            "[ERROR] action_dispatch_failed "
            f"run_id={self.run_id or 'unknown'} "
            f"agent_id={self.agent_id or 'unknown'} "
            f"action_type={action_type} "
            f"action_name={action_name} "
            f"method={method or 'unknown'} "
            f"path={path or 'unknown'} "
            f"status_code={status_code if status_code is not None else 'none'} "
            f"request_id={request_id or 'none'} "
            f"error_code={error_code} "
            f"error={error}"
        )
    
    async def _execute_http(self, action: Action) -> Dict[str, Any]:
        """Execute an HTTP action with v1 IA two-phase protocol support."""
        action_name = self._resolve_action_name(action)
        method = action.method or HTTPMethod.GET
        action_type = self._resolve_action_type(action)
        action_key = self._resolve_action_key(action, action_name)

        if not action.url:
            error_msg = "URL is required for HTTP action"
            self._log_dispatch_failure(
                action_type=action_type,
                action_name=action_name,
                method=method.value,
                path=action.url,
                status_code=None,
                request_id=None,
                error_code="invalid_http_action",
                error=error_msg,
            )
            return {
                "action": "http",
                "action_type": action_type,
                "action_name": action_name,
                "action_key": action_key,
                "success": False,
                "error": error_msg,
                "error_code": "invalid_http_action",
                "method": method.value,
                "path": action.url,
                "status_code": None,
                "request_id": None,
                "timestamp": datetime.now().isoformat(),
            }

        normalized_url = self._normalize_http_url(action.url)
        if normalized_url and normalized_url != action.url:
            action.url = normalized_url

        if not self._is_url_allowed(action.url):
            error_msg = f"Egress restricted for target URL: {action.url}"
            self._log_dispatch_failure(
                action_type=action_type,
                action_name=action_name,
                method=method.value,
                path=action.url,
                status_code=None,
                request_id=None,
                error_code="egress_restricted",
                error=error_msg,
            )
            return {
                "action": "http",
                "action_type": action_type,
                "action_name": action_name,
                "action_key": action_key,
                "success": False,
                "error": error_msg,
                "error_code": "egress_restricted",
                "method": method.value,
                "path": action.url,
                "status_code": None,
                "request_id": None,
                "timestamp": datetime.now().isoformat(),
            }
        
        headers = self._inject_runtime_headers(
            action.headers or {},
            target_url=action.url,
        )
        is_registration_call = self._is_registration_endpoint(action.url)
        requires_auth = self._is_mutating_method(method) and not is_registration_call
        has_auth_header = self._has_header(headers, "Authorization")
        target_hosts = set(self._host_variants(action.url))

        if requires_auth and not has_auth_header:
            is_primary_target = bool(target_hosts.intersection(self.primary_environment_hosts))
            if is_primary_target:
                error_code = "missing_primary_environment_auth"
                error_msg = (
                    f"Missing host-scoped auth token for primary environment target: {action.url}"
                )
            else:
                error_code = "unregistered_secondary_environment"
                error_msg = (
                    "Secondary environment write requires registration first "
                    f"(POST /auth/register) for target: {action.url}"
                )
            self._log_dispatch_failure(
                action_type=action_type,
                action_name=action_name,
                method=method.value,
                path=action.url,
                status_code=None,
                request_id=None,
                error_code=error_code,
                error=error_msg,
            )
            return {
                "action": "http",
                "action_type": action_type,
                "action_name": action_name,
                "action_key": action_key,
                "success": False,
                "error": error_msg,
                "error_code": error_code,
                "method": method.value,
                "path": action.url,
                "status_code": None,
                "request_id": None,
                "timestamp": datetime.now().isoformat(),
            }
        body = self._resolve_runtime_placeholders(action.body)
        
        return await self._execute_direct_http(action, method, headers, body)
    
    async def _execute_direct_http(
        self,
        action: Action,
        method: HTTPMethod,
        headers: Dict[str, str],
        body: Optional[Any]
    ) -> Dict[str, Any]:
        """Execute HTTP action directly without IA."""
        action_name = self._resolve_action_name(action)
        action_type = self._resolve_action_type(action)
        action_key = self._resolve_action_key(action, action_name)
        try:
            request_kwargs: Dict[str, Any] = {
                "method": method.value,
                "url": action.url,
                "headers": headers,
            }
            if body is not None:
                if isinstance(body, str):
                    request_kwargs["content"] = body
                else:
                    request_kwargs["json"] = body

            response = await self.http_client.request(**request_kwargs)
            request_id = self._extract_request_id(dict(response.headers))
            
            response_data = {
                "status_code": response.status_code,
                "headers": dict(response.headers),
            }
            
            # Try to parse as JSON
            try:
                response_body = response.json()
                response_data["body"] = response_body
            except Exception:
                response_data["body"] = response.text

            auth_registry_updated = False
            auth_registry_host: Optional[str] = None
            if response.status_code < 300 and self._is_registration_endpoint(action.url):
                api_token = self._extract_api_token(response_data.get("body"))
                if api_token:
                    auth_registry_updated = self._register_host_token(
                        action.url,
                        api_token,
                        source="agent_action_register",
                    )
                    await self._persist_registration_credentials(response_data.get("body"))
                    if auth_registry_updated:
                        host_variants = self._host_variants(action.url)
                        auth_registry_host = host_variants[0] if host_variants else None

            # Legacy two-phase support (deprecated)
            if response.status_code == 200:
                try:
                    json_body = response.json() if isinstance(response_data.get("body"), dict) else None
                    if json_body and json_body.get("status") == "pending_confirmation":
                        confirmation_code = json_body.get("confirmation_code")
                        if confirmation_code:
                            confirm_response = await self._send_legacy_confirmation(
                                action.url, confirmation_code, headers
                            )
                            return {
                                "action": "http",
                                "success": confirm_response.get("success", False),
                                "action_type": action_type,
                                "action_name": action_name,
                                "action_key": action_key,
                                "phase": "legacy_confirmed",
                                "method": method.value,
                                "path": action.url,
                                "initial_response": response_data,
                                "confirmation_response": confirm_response,
                                "timestamp": datetime.now().isoformat(),
                            }
                except Exception:
                    pass
            
            if not (200 <= response.status_code < 300):
                error_detail: Optional[str] = None
                body_payload = response_data.get("body")
                if isinstance(body_payload, dict):
                    detail = body_payload.get("detail") or body_payload.get("error") or body_payload.get("message")
                    if isinstance(detail, (dict, list)):
                        error_detail = json.dumps(detail, ensure_ascii=True)
                    elif detail:
                        error_detail = str(detail)
                elif body_payload is not None:
                    candidate = str(body_payload).strip()
                    if candidate:
                        error_detail = candidate[:500]

                self._log_dispatch_failure(
                    action_type=action_type,
                    action_name=action_name,
                    method=method.value,
                    path=action.url,
                    status_code=response.status_code,
                    request_id=request_id,
                    error_code=f"http_status_{response.status_code}",
                    error=error_detail or f"HTTP {response.status_code}",
                )

            return {
                "action": "http",
                "success": 200 <= response.status_code < 300,
                "action_type": action_type,
                "action_name": action_name,
                "action_key": action_key,
                "error": None if 200 <= response.status_code < 300 else (
                    error_detail or f"HTTP {response.status_code}"
                ),
                "error_code": None if 200 <= response.status_code < 300 else f"http_status_{response.status_code}",
                "method": method.value,
                "path": action.url,
                "status_code": response.status_code,
                "request_id": request_id,
                "response": response_data,
                "auth_registry_updated": auth_registry_updated,
                "auth_registry_host": auth_registry_host,
                "timestamp": datetime.now().isoformat(),
            }
        
        except httpx.TimeoutException:
            error_msg = "Request timeout"
            self._log_dispatch_failure(
                action_type=action_type,
                action_name=action_name,
                method=method.value,
                path=action.url,
                status_code=None,
                request_id=None,
                error_code="request_timeout",
                error=error_msg,
            )
            return {
                "action": "http",
                "success": False,
                "action_type": action_type,
                "action_name": action_name,
                "action_key": action_key,
                "error": error_msg,
                "error_code": "request_timeout",
                "method": method.value,
                "path": action.url,
                "status_code": None,
                "request_id": None,
                "timestamp": datetime.now().isoformat(),
            }
        
        except httpx.RequestError as e:
            error_msg = f"Request error: {str(e)}"
            self._log_dispatch_failure(
                action_type=action_type,
                action_name=action_name,
                method=method.value,
                path=action.url,
                status_code=None,
                request_id=None,
                error_code="request_error",
                error=error_msg,
            )
            return {
                "action": "http",
                "success": False,
                "action_type": action_type,
                "action_name": action_name,
                "action_key": action_key,
                "error": error_msg,
                "error_code": "request_error",
                "method": method.value,
                "path": action.url,
                "status_code": None,
                "request_id": None,
                "timestamp": datetime.now().isoformat(),
            }
    
    async def _send_legacy_confirmation(
        self,
        base_url: str,
        confirmation_code: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Send legacy confirmation for backward compatibility."""
        confirm_url = base_url
        if not confirm_url.endswith("/confirm"):
            confirm_url = confirm_url.rstrip("/") + "/confirm"
        
        try:
            response = await self.http_client.post(
                confirm_url,
                json={"confirmation_code": confirmation_code},
                headers=headers,
            )
            
            try:
                body = response.json()
            except Exception:
                body = response.text
            
            return {
                "success": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "body": body,
            }
        
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
    
    async def _execute_filesystem(self, action: Action) -> Dict[str, Any]:
        """Execute a filesystem action."""
        if not action.path:
            return {
                "action": "fs",
                "success": False,
                "action_type": self._resolve_action_type(action),
                "action_name": self._resolve_action_name(action),
                "error": "Path is required for filesystem action",
                "error_code": "fs_missing_path",
                "timestamp": datetime.now().isoformat(),
            }
        
        operation = action.operation or "write"
        
        try:
            # Security: writes are workspace-only; reads can also access skills/.
            target_path = self.agent_fs._resolve_file_path(action.path)
            target_resolved = target_path.resolve()
            workspace_root = self.agent_fs.workspace_path.resolve()
            skills_root = self.agent_fs.skills_path.resolve()

            in_workspace = False
            in_skills = False
            try:
                target_resolved.relative_to(workspace_root)
                in_workspace = True
            except ValueError:
                pass
            try:
                target_resolved.relative_to(skills_root)
                in_skills = True
            except ValueError:
                pass

            skill_state_path = self._is_skill_state_path(target_resolved, skills_root)

            if operation == "read":
                if not (in_workspace or in_skills):
                    return {
                        "action": "fs",
                        "success": False,
                        "action_type": self._resolve_action_type(action),
                        "action_name": self._resolve_action_name(action),
                        "error": "Path is outside allowed workspace/skills read scope",
                        "error_code": "fs_path_outside_workspace",
                        "timestamp": datetime.now().isoformat(),
                    }
            else:
                if not (in_workspace or skill_state_path):
                    return {
                        "action": "fs",
                        "success": False,
                        "action_type": self._resolve_action_type(action),
                        "action_name": self._resolve_action_name(action),
                        "error": "Path is outside mutable workspace or skill-state scope",
                        "error_code": "fs_path_outside_workspace",
                        "timestamp": datetime.now().isoformat(),
                    }
            
            if operation == "write":
                await self.agent_fs.write_file(action.path, action.content or "")
                return {
                    "action": "fs",
                    "success": True,
                    "action_type": self._resolve_action_type(action),
                    "action_name": self._resolve_action_name(action),
                    "error_code": None,
                    "operation": "write",
                    "path": action.path,
                    "timestamp": datetime.now().isoformat(),
                }
            
            elif operation == "append":
                await self.agent_fs.append_file(action.path, action.content or "")
                return {
                    "action": "fs",
                    "success": True,
                    "action_type": self._resolve_action_type(action),
                    "action_name": self._resolve_action_name(action),
                    "error_code": None,
                    "operation": "append",
                    "path": action.path,
                    "timestamp": datetime.now().isoformat(),
                }
            
            elif operation == "read":
                content = await self.agent_fs.read_file(action.path)
                return {
                    "action": "fs",
                    "success": content is not None,
                    "action_type": self._resolve_action_type(action),
                    "action_name": self._resolve_action_name(action),
                    "error_code": None if content is not None else "fs_read_not_found",
                    "operation": "read",
                    "path": action.path,
                    "content": content,
                    "timestamp": datetime.now().isoformat(),
                }

            elif operation == "edit":
                if action.find is None or action.replace is None:
                    return {
                        "action": "fs",
                        "success": False,
                        "action_type": self._resolve_action_type(action),
                        "action_name": self._resolve_action_name(action),
                        "error": "find and replace are required for edit",
                        "error_code": "fs_edit_missing_arguments",
                        "timestamp": datetime.now().isoformat(),
                    }
                content = await self.agent_fs.read_file(action.path)
                if content is None:
                    return {
                        "action": "fs",
                        "success": False,
                        "action_type": self._resolve_action_type(action),
                        "action_name": self._resolve_action_name(action),
                        "error": "File not found for edit",
                        "error_code": "fs_edit_not_found",
                        "path": action.path,
                        "timestamp": datetime.now().isoformat(),
                    }
                replace_all = bool(action.replace_all)
                if action.find not in content:
                    return {
                        "action": "fs",
                        "success": False,
                        "action_type": self._resolve_action_type(action),
                        "action_name": self._resolve_action_name(action),
                        "error": "Find text not found",
                        "error_code": "fs_edit_find_not_found",
                        "path": action.path,
                        "timestamp": datetime.now().isoformat(),
                    }
                if replace_all:
                    updated = content.replace(action.find, action.replace)
                    replacements = content.count(action.find)
                else:
                    updated = content.replace(action.find, action.replace, 1)
                    replacements = 1
                await self.agent_fs.write_file(action.path, updated)
                return {
                    "action": "fs",
                    "success": True,
                    "action_type": self._resolve_action_type(action),
                    "action_name": self._resolve_action_name(action),
                    "error_code": None,
                    "operation": "edit",
                    "path": action.path,
                    "replacements": replacements,
                    "replace_all": replace_all,
                    "timestamp": datetime.now().isoformat(),
                }

            elif operation == "delete":
                deleted = await self.agent_fs.delete_file(action.path)
                return {
                    "action": "fs",
                    "success": deleted,
                    "action_type": self._resolve_action_type(action),
                    "action_name": self._resolve_action_name(action),
                    "error": None if deleted else "File not found for delete",
                    "error_code": None if deleted else "fs_delete_not_found",
                    "operation": "delete",
                    "path": action.path,
                    "timestamp": datetime.now().isoformat(),
                }
            
            else:
                return {
                    "action": "fs",
                    "success": False,
                    "action_type": self._resolve_action_type(action),
                    "action_name": self._resolve_action_name(action),
                    "error": f"Unknown operation: {operation}",
                    "error_code": "fs_unknown_operation",
                    "timestamp": datetime.now().isoformat(),
                }
        
        except Exception as e:
            return {
                "action": "fs",
                "success": False,
                "action_type": self._resolve_action_type(action),
                "action_name": self._resolve_action_name(action),
                "error": str(e),
                "error_code": "fs_execution_exception",
                "operation": operation,
                "path": action.path,
                "timestamp": datetime.now().isoformat(),
            }
    
    async def _log_action_result(
        self,
        action: Action,
        result: Dict[str, Any]
    ) -> None:
        """Log action execution result to agent HEARTBEAT.md."""
        log_entry = f"""### Action Execution: {action.action}

- **Timestamp**: {result.get('timestamp')}
- **Success**: {result.get('success')}
"""

        if action.description:
            log_entry += f"- **Description**: {action.description}\n"

        if action.action == ActionType.HTTP and action.url:
            log_entry += f"- **URL**: {action.url}\n"
            log_entry += f"- **Method**: {result.get('method')}\n"
            log_entry += f"- **Status Code**: {result.get('status_code')}\n"
            log_entry += f"- **Request ID**: {result.get('request_id')}\n"
            response_payload = result.get("response")
            if isinstance(response_payload, dict):
                body_payload = response_payload.get("body")
                if body_payload is not None:
                    if isinstance(body_payload, (dict, list)):
                        body_preview = json.dumps(body_payload, ensure_ascii=True)
                    else:
                        body_preview = str(body_payload)
                    if len(body_preview) > 1200:
                        body_preview = body_preview[:1200] + "...<truncated>"
                    log_entry += f"- **Response Body**: {body_preview}\n"

        if action.action == ActionType.FILESYSTEM and action.path:
            log_entry += f"- **Path**: {action.path}\n"

        if result.get("action_name"):
            log_entry += f"- **Action Name**: {result.get('action_name')}\n"

        if result.get("action_type"):
            log_entry += f"- **Action Type**: {result.get('action_type')}\n"

        if result.get("action_key"):
            log_entry += f"- **Action Key**: {result.get('action_key')}\n"

        if result.get('error'):
            log_entry += f"- **Error**: {result['error']}\n"

        log_entry += "\n"

        # Keep runtime action logs out of the prompt-visible HEARTBEAT.md file.
        await self.agent_fs.append_runtime_heartbeat_entry(log_entry)

    async def _record_action_telemetry(
        self,
        action: Action,
        result: Dict[str, Any]
    ) -> None:
        """Record structured per-action telemetry from the active execution path."""
        try:
            action_type = result.get("action_type") or self._resolve_action_type(action)
            action_name = result.get("action_name") or self._resolve_action_name(action)
            action_key = result.get("action_key") or self._resolve_action_key(action, action_name)
            success = bool(result.get("success"))
            duration_ms = result.get("duration_ms")
            error_message = result.get("error")
            error_code = result.get("error_code")
            if not success and not error_message:
                response_payload = result.get("response")
                if isinstance(response_payload, dict):
                    body = response_payload.get("body")
                    if isinstance(body, dict):
                        detail = body.get("detail") or body.get("error") or body.get("message")
                        if isinstance(detail, (dict, list)):
                            error_message = json.dumps(detail, ensure_ascii=True)
                        elif detail:
                            error_message = str(detail)
                    elif body is not None:
                        candidate = str(body).strip()
                        if candidate:
                            error_message = candidate[:500]
            if not success and not error_code:
                error_code = "action_failed"

            response_preview = None
            response_body_chars = None
            response_payload = result.get("response")
            if isinstance(response_payload, dict):
                body = response_payload.get("body")
                if body is not None:
                    if isinstance(body, (dict, list)):
                        response_preview = json.dumps(body, ensure_ascii=True)
                    else:
                        response_preview = str(body)
                    response_preview = response_preview.strip()
                    response_body_chars = len(response_preview)
                    if len(response_preview) > 700:
                        response_preview = f"{response_preview[:697]}..."

            action_url = None
            if hasattr(action, "url"):
                try:
                    action_url = getattr(action, "url")
                except Exception:
                    action_url = None

            payload = {
                "event_type": "action_attempt",
                "tick": self.heartbeat_tick,
                "heartbeat_index": self.heartbeat_index,
                "action_type": action_type,
                "action_name": action_name,
                "action_key": action_key,
                "method": result.get("method"),
                "url": result.get("url") or action_url,
                "path": result.get("path"),
                "status_code": result.get("status_code"),
                "request_id": result.get("request_id"),
                "operation": action.operation,
                "error_code": error_code if not success else None,
                "response_preview": response_preview,
                "response_body_chars": response_body_chars,
            }

            await record_action(
                run_id=self.run_id or "unknown",
                agent_id=self.agent_id,
                action_type=action_type,
                action_category=self._resolve_action_category(action),
                success=success,
                duration_ms=duration_ms,
                payload=payload,
                error_message=error_message,
            )
        except Exception as e:
            print(
                f"[WARNING] Failed to record action telemetry "
                f"(run_id={self.run_id}, agent_id={self.agent_id}): {e}"
            )
