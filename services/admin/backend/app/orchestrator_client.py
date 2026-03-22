"""HTTP clients for external services."""

import os
from typing import Optional
from urllib.parse import quote
import httpx

from .config import Settings


class ControllerProxyError(RuntimeError):
    """Structured upstream controller error with HTTP status preservation."""

    def __init__(self, status_code: int, detail):
        self.status_code = int(status_code)
        self.detail = detail
        super().__init__(str(detail))


class ControllerClient:
    """Client for interacting with the run controller."""
    RUN_CONTROL_TIMEOUT_SECONDS = 120.0
    
    def __init__(self, settings: Settings):
        """Initialize the client with settings."""
        self.base_url = settings.controller_url.rstrip("/")
        self.timeout = httpx.Timeout(settings.http_timeout)
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _extract_response_detail(response: httpx.Response):
        """Return best-effort error detail from an upstream response."""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return payload.get("detail", payload)
            return payload
        except Exception:
            text = (response.text or "").strip()
            return text or response.reason_phrase or "Controller request failed"

    async def _request_controller(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
        timeout: Optional[float] = None,
    ):
        """Issue controller request and preserve upstream status/details."""
        client = await self._get_client()
        try:
            response = await client.request(
                method=method.upper(),
                url=f"{self.base_url}{path}",
                params=params,
                json=payload,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except httpx.TimeoutException:
            raise RuntimeError("Controller request timed out")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

        if response.status_code >= 400:
            raise ControllerProxyError(
                status_code=response.status_code,
                detail=self._extract_response_detail(response),
            )

        if response.status_code == 204 or not response.text.strip():
            return {}

        try:
            return response.json()
        except Exception:
            return {"detail": response.text}

    @staticmethod
    def _encode(value: str) -> str:
        return quote(str(value), safe="")

    @staticmethod
    def _encode_file_path(value: str) -> str:
        return quote(str(value).strip("/"), safe="/")
    
    async def list_environments(self) -> list[dict]:
        """List canonical environment manifests."""
        data = await self._request_controller("GET", "/api/v1/environments")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            environments = data.get("environments")
            if isinstance(environments, list):
                return environments
        return []

    async def get_environment(self, environment_id: str) -> dict:
        safe_id = self._encode(environment_id)
        return await self._request_controller("GET", f"/api/v1/environments/{safe_id}")

    async def validate_environment(self, environment_id: str) -> dict:
        safe_id = self._encode(environment_id)
        return await self._request_controller(
            "POST",
            f"/api/v1/environments/{safe_id}/validate",
        )

    async def list_runtimes(self) -> list[dict]:
        data = await self._request_controller("GET", "/api/v1/runtimes")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            runtimes = data.get("runtimes")
            if isinstance(runtimes, list):
                return runtimes
        return []

    async def get_runtime(self, runtime_id: str) -> dict:
        safe_id = self._encode(runtime_id)
        return await self._request_controller("GET", f"/api/v1/runtimes/{safe_id}")

    async def health_check(self) -> dict:
        """Check controller health."""
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.base_url}/health",
                timeout=5.0
            )
            response.raise_for_status()
            return {"status": "healthy", "details": response.json()}
        except (httpx.ConnectError, httpx.TimeoutException):
            return {"status": "unhealthy", "error": "Cannot connect to controller"}
        except httpx.HTTPStatusError as e:
            return {"status": "degraded", "error": f"HTTP {e.response.status_code}"}

    # Run methods
    async def create_run(self, run_data: dict) -> dict:
        """Create a new run from an environment manifest."""
        return await self._request_controller("POST", "/api/v1/runs", payload=run_data)

    async def list_runs(
        self,
        *,
        environment_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """List runs with optional filters."""
        params = {}
        if environment_id:
            params["environment_id"] = environment_id
        if status:
            params["status"] = status
        data = await self._request_controller("GET", "/api/v1/runs", params=params or None)
        return data if isinstance(data, list) else []

    async def get_run(self, run_id: str) -> dict:
        """Get a specific run by ID."""
        safe_id = self._encode(run_id)
        return await self._request_controller("GET", f"/api/v1/runs/{safe_id}")

    async def get_run_snapshot(self, run_id: str) -> dict:
        safe_id = self._encode(run_id)
        return await self._request_controller("GET", f"/api/v1/runs/{safe_id}/snapshot")

    async def get_run_scheduler_status(self, run_id: str) -> dict:
        """Get scheduler status for a run."""
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.base_url}/api/v1/runs/{run_id}/scheduler/status"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

    async def get_run_cost(self, run_id: str) -> dict:
        """Get aggregated run cost + usage."""
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.base_url}/api/v1/runs/{run_id}/cost"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

    async def get_run_compass_review(
        self,
        run_id: str,
        history_limit: Optional[int] = None,
        event_limit: Optional[int] = None,
    ) -> dict:
        """Get reviewer-friendly compass aggregation for a run."""
        params: dict[str, int] = {}
        if history_limit is not None:
            params["history_limit"] = history_limit
        if event_limit is not None:
            params["event_limit"] = event_limit
        return await self._request_controller(
            "GET",
            f"/api/v1/runs/{run_id}/compass-review",
            params=params or None,
        )

    async def get_run_condition_manifest(self, run_id: str, mode: Optional[str] = None) -> dict:
        """Get canonical condition manifest JSON for a run."""
        params = {"mode": mode} if mode else None
        return await self._request_controller(
            "GET",
            f"/api/v1/runs/{run_id}/condition-manifest",
            params=params,
        )

    async def get_run_condition_manifest_csv(self, run_id: str, mode: Optional[str] = None) -> dict:
        """Get condition manifest CSV for a run."""
        client = await self._get_client()
        params = {"mode": mode} if mode else None

        try:
            response = await client.get(
                f"{self.base_url}/api/v1/runs/{run_id}/condition-manifest.csv",
                params=params,
            )
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")
        except httpx.TimeoutException:
            raise RuntimeError("Controller request timed out")

        if response.status_code >= 400:
            raise ControllerProxyError(
                status_code=response.status_code,
                detail=self._extract_response_detail(response),
            )

        return {
            "content": response.text,
            "content_disposition": response.headers.get("content-disposition"),
        }

    async def get_governance_snapshot(
        self,
        run_limit: Optional[int] = None,
        events_per_run: Optional[int] = None,
        cost_samples: Optional[int] = None,
    ) -> dict:
        """Get aggregated governance diagnostics snapshot."""
        client = await self._get_client()

        params: dict[str, int] = {}
        if run_limit is not None:
            params["run_limit"] = run_limit
        if events_per_run is not None:
            params["events_per_run"] = events_per_run
        if cost_samples is not None:
            params["cost_samples"] = cost_samples

        try:
            response = await client.get(
                f"{self.base_url}/api/v1/governance/snapshot",
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

    async def get_run_agent_context(self, run_id: str, limit_per_agent: int = 20) -> dict:
        """Get explainability context for agents in a run."""
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.base_url}/api/v1/runs/{run_id}/agent-context",
                params={"limit_per_agent": limit_per_agent},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

    async def export_agent_context(self, run_id: str, agent_id: str, tick: Optional[int] = None) -> dict:
        """Export model-facing context chain for one agent up to tick N."""
        client = await self._get_client()
        params = {}
        if tick is not None:
            params["tick"] = tick
        try:
            response = await client.get(
                f"{self.base_url}/api/v1/runs/{run_id}/agents/{agent_id}/context-export",
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")
    
    async def stop_run(self, run_id: str) -> dict:
        """Stop a specific run."""
        safe_id = self._encode(run_id)
        return await self._request_controller(
            "POST",
            f"/api/v1/runs/{safe_id}/stop",
            timeout=self.RUN_CONTROL_TIMEOUT_SECONDS,
        )

    async def restart_run_stack(self, run_id: str) -> dict:
        """Relaunch the per-run stack for a finished run."""
        safe_id = self._encode(run_id)
        return await self._request_controller(
            "POST",
            f"/api/v1/runs/{safe_id}/restart-stack",
            timeout=self.RUN_CONTROL_TIMEOUT_SECONDS,
        )

    async def pause_run(self, run_id: str) -> dict:
        """Pause a specific run."""
        safe_id = self._encode(run_id)
        return await self._request_controller(
            "POST",
            f"/api/v1/runs/{safe_id}/pause",
        )

    async def resume_run(self, run_id: str) -> dict:
        """Resume a specific paused run."""
        safe_id = self._encode(run_id)
        return await self._request_controller(
            "POST",
            f"/api/v1/runs/{safe_id}/resume",
        )

    async def delete_run(self, run_id: str) -> dict:
        """Delete a specific run."""
        client = await self._get_client()
        
        try:
            response = await client.delete(
                f"{self.base_url}/api/v1/runs/{run_id}"
            )
            if response.status_code in (200, 204):
                if not response.text.strip():
                    return {"deleted": True, "run_id": run_id}
                try:
                    return response.json()
                except ValueError:
                    return {"deleted": True, "run_id": run_id}

            if response.status_code == 404:
                return {"deleted": False, "run_id": run_id, "message": "Run already absent"}

            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

    # Event methods
    async def get_run_events(
        self,
        run_id: str,
        event_type: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Get events for a run from the controller."""
        client = await self._get_client()

        params: dict[str, object] = {"limit": limit, "offset": offset}
        if event_type:
            params["event_type"] = event_type
        if agent_id:
            params["agent_id"] = agent_id

        try:
            response = await client.get(
                f"{self.base_url}/api/v1/events/runs/{run_id}",
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

    async def get_run_event_count(self, run_id: str) -> dict:
        """Get event count for a run from the controller."""
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.base_url}/api/v1/events/runs/{run_id}/count"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")

    async def export_events(self, request_data: dict) -> dict:
        """Export events for one or more runs."""
        client = await self._get_client()

        try:
            response = await client.post(
                f"{self.base_url}/api/v1/events/export",
                json=request_data,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Controller unavailable")


class OrchestratorClient:
    """Client for interacting with the environment orchestrator."""
    
    def __init__(self, settings: Settings):
        """Initialize the client with settings."""
        self.base_url = settings.orchestrator_url.rstrip('/')
        self.controller_url = settings.controller_url.rstrip("/")
        self.timeout = httpx.Timeout(settings.http_timeout)
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    async def get_environment_status(self, name: str) -> dict:
        """Get status of a specific environment."""
        client = await self._get_client()
        
        try:
            response = await client.get(f"{self.base_url}/orchestrator/environments/{name}/status")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"name": name, "status": "stopped", "services": [], "urls": {}, "health": {}}
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Orchestrator unavailable")

    @staticmethod
    def _environment_image_ref(environment_id: str, image_spec: dict, namespace: str) -> str:
        repository = str(image_spec.get("repository") or "").strip()
        if not repository:
            kind = str(image_spec.get("kind") or "backend").strip() or "backend"
            suffix = "" if kind == "backend" else f"-{kind}"
            repository = f"mase-{environment_id}{suffix}"
        return f"{repository}:{namespace}"

    @staticmethod
    def _standalone_frontend_port(environment_id: str) -> int:
        normalized = str(environment_id or "environment").strip() or "environment"
        return 18500 + (sum(ord(ch) for ch in normalized) % 300)

    def _environment_launch_env(self, environment_info: dict) -> tuple[dict[str, str], Optional[str], str]:
        env_vars: dict[str, str] = {}
        environment_id = str(environment_info.get("id") or environment_info.get("name") or "").strip()
        environment_name = str(environment_info.get("name") or environment_id).strip() or environment_id
        launch = environment_info.get("launch") if isinstance(environment_info.get("launch"), dict) else {}
        images = launch.get("images") if isinstance(launch.get("images"), dict) else {}

        compose_project = str(os.getenv("COMPOSE_PROJECT_NAME", "")).strip()
        if compose_project:
            env_vars["COMPOSE_PROJECT_NAME"] = compose_project

        network_name = str(os.getenv("MASE_NETWORK_NAME", "")).strip()
        if network_name:
            env_vars["MASE_NETWORK_NAME"] = network_name

        if environment_id:
            env_vars["ENVIRONMENT_ID"] = environment_id
        if environment_name:
            env_vars["ENVIRONMENT_NAME"] = environment_name

        if compose_project and isinstance(images.get("backend"), dict):
            env_vars["ENVIRONMENT_BACKEND_IMAGE"] = self._environment_image_ref(
                environment_id,
                images["backend"],
                compose_project,
            )
        if compose_project and isinstance(images.get("frontend"), dict):
            env_vars["ENVIRONMENT_FRONTEND_IMAGE"] = self._environment_image_ref(
                environment_id,
                images["frontend"],
                compose_project,
            )
        if launch.get("frontend_service"):
            env_vars["ENVIRONMENT_FRONTEND_PORT"] = str(self._standalone_frontend_port(environment_id))

        compose_path = str(
            ((launch.get("environment_service") or {}) if isinstance(launch.get("environment_service"), dict) else {}).get("compose_file")
            or f"environments/{environment_id}/compose.run.yml"
        ).strip()
        return env_vars, None, compose_path
    
    async def start_environment(self, name: str) -> dict:
        """Start an environment."""
        client = await self._get_client()
        
        try:
            # Get environment info from registry first
            response = await client.get(f"{self.controller_url}/api/v1/environments")
            data = response.json()
            env_info = None
            environments = data if isinstance(data, list) else data.get("environments", [])
            for env in environments:
                if env.get("id") == name or env.get("name") == name:
                    env_info = env
                    break
            
            if not env_info:
                raise RuntimeError(f"Environment {name} not found in registry")

            env_vars, derived_health_url, compose_path = self._environment_launch_env(env_info)
            
            # Start via orchestrator
            response = await client.post(
                f"{self.base_url}/orchestrator/environments/{name}/start",
                json={
                    "compose_path": compose_path,
                    "health_url": derived_health_url or env_info.get("health_url"),
                    "env_vars": env_vars,
                    "urls": {}
                },
                timeout=120.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Orchestrator unavailable")
    
    async def stop_environment(self, name: str) -> dict:
        """Stop an environment."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.base_url}/orchestrator/environments/{name}/stop"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Controller error: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Orchestrator unavailable")
    
    async def health_check(self) -> dict:
        """Check orchestrator health."""
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.base_url}/health",
                timeout=5.0
            )
            response.raise_for_status()
            return {"status": "healthy", "details": response.json()}
        except (httpx.ConnectError, httpx.TimeoutException):
            return {"status": "unhealthy", "error": "Cannot connect to orchestrator"}
        except httpx.HTTPStatusError as e:
            return {"status": "degraded", "error": f"HTTP {e.response.status_code}"}
