"""Run launcher for per-run Docker Compose orchestration.

Manages starting and stopping run-specific services via the orchestrator service,
which has proper Docker socket access.
"""

import asyncio
import hashlib
import logging
import os
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
import httpx

from app.catalog import discover_environments, get_environment
from app.config import settings


class RunLaunchConfig(BaseModel):
    """Configuration for launching a run."""

    run_id: str = Field(..., description="Run identifier")
    environment_id: str = Field(..., description="Environment identifier")
    institutional_mode: bool = Field(default=False, description="Reserved; public v1 launches do not include extra services")
    seed: Optional[int] = Field(None, description="Random seed")
    resolved_bundle_hash: Optional[str] = Field(None, description="Bundle hash")
    api_key: Optional[str] = Field(None, description="LLM API key for agents")


class RunLauncher:
    """Launches runs via the orchestrator service.
    
    Delegates container orchestration to the orchestrator service which has
    proper Docker socket access, rather than trying to run docker commands
    directly from within the controller container.
    """

    FRONTEND_PORT_BASE = 18080
    FRONTEND_PORT_SPAN = 800
    RUNTIME_IMAGE_ENV_KEYS = (
        "AGENT_WORKER_IMAGE",
    )

    def __init__(self, compose_base_path: str = "./compose"):
        """Initialize launcher.

        Args:
            compose_base_path: Base path for compose files
        """
        self.compose_base_path = compose_base_path
        self.stacks_path = os.path.join(compose_base_path, "stacks")
        self.overrides_path = os.path.join(compose_base_path, "stacks", "run-overrides")
        self.orchestrator_url = settings.orchestrator_url
        self.logger = logging.getLogger(__name__)

        # Ensure directories exist
        os.makedirs(self.overrides_path, exist_ok=True)

    def _cleanup_override_artifacts(self, run_id: str) -> None:
        """Remove generated override files for a run."""
        override_file = os.path.join(self.overrides_path, f"{run_id}.yml")
        if os.path.exists(override_file):
            try:
                os.remove(override_file)
            except OSError as e:
                print(f"Warning: Failed to remove override file {override_file}: {e}")

        prefix = f"{run_id}__oracle__"
        for entry in os.listdir(self.overrides_path):
            if not entry.startswith(prefix):
                continue
            path = os.path.join(self.overrides_path, entry)
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
            except OSError as e:
                print(f"Warning: Failed to remove Oracle bundle artifact {path}: {e}")

    @staticmethod
    def _legacy_runtime_alias_env(
        *,
        state_key: str,
        state_path: str,
        frontend_port: int,
        frontend_url: str,
    ) -> Dict[str, str]:
        """Build optional legacy alias env vars from configurable keys."""
        alias_payload: Dict[str, str] = {}
        alias_entries = (
            (settings.legacy_state_key_env_var, state_key),
            (settings.legacy_state_path_env_var, state_path),
            (settings.legacy_frontend_port_env_var, str(frontend_port)),
            (settings.legacy_frontend_url_env_var, frontend_url),
        )
        for key, value in alias_entries:
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            alias_payload[normalized_key] = value
        return alias_payload

    @classmethod
    def get_runtime_namespace(cls) -> str:
        """Get the worktree/runtime namespace used for deterministic launch assets."""
        raw = str(
            os.getenv("COMPOSE_PROJECT_NAME")
            or os.getenv("MASE_NETWORK_NAME")
            or "mase"
        ).strip()
        if raw.endswith("-network"):
            raw = raw[: -len("-network")]
        return raw or "mase"

    @classmethod
    def get_environment_frontend_port(cls, run_id: str, namespace: Optional[str] = None) -> int:
        """Get deterministic host port for a run frontend preview."""
        normalized_namespace = str(namespace or "").strip() or cls.get_runtime_namespace()
        digest = hashlib.sha1(f"{normalized_namespace}:{run_id}".encode("utf-8")).hexdigest()
        offset = int(digest[:6], 16) % cls.FRONTEND_PORT_SPAN
        return cls.FRONTEND_PORT_BASE + offset

    @classmethod
    def get_environment_frontend_url(cls, run_id: str, namespace: Optional[str] = None) -> str:
        """Get browser-facing frontend URL for a run."""
        return f"http://localhost:{cls.get_environment_frontend_port(run_id, namespace=namespace)}"

    @classmethod
    def default_environment_image_repository(cls, environment_id: str, asset_kind: str) -> str:
        normalized_environment = str(environment_id or "").strip() or "environment"
        suffix = "" if asset_kind == "backend" else f"-{asset_kind}"
        return f"mase-{normalized_environment}{suffix}"

    @classmethod
    def get_environment_image_ref(
        cls,
        environment_id: str,
        image_spec: Dict[str, Any],
        *,
        namespace: Optional[str] = None,
    ) -> str:
        repository = str(image_spec.get("repository") or "").strip()
        if not repository:
            repository = cls.default_environment_image_repository(
                environment_id,
                str(image_spec.get("kind") or "backend").strip() or "backend",
            )
        normalized_namespace = str(namespace or "").strip() or cls.get_runtime_namespace()
        return f"{repository}:{normalized_namespace}"

    @classmethod
    def get_run_image_env_vars(
        cls,
        environment_id: str,
        launch_config: Dict[str, Any],
    ) -> Dict[str, str]:
        payload: Dict[str, str] = {}
        agent_worker_image = str(os.getenv("AGENT_WORKER_IMAGE") or "").strip()
        if agent_worker_image:
            payload["AGENT_WORKER_IMAGE"] = agent_worker_image

        images = launch_config.get("images") if isinstance(launch_config.get("images"), dict) else {}
        backend_spec = images.get("backend") if isinstance(images.get("backend"), dict) else None
        frontend_spec = images.get("frontend") if isinstance(images.get("frontend"), dict) else None

        if backend_spec:
            payload["ENVIRONMENT_BACKEND_IMAGE"] = cls.get_environment_image_ref(environment_id, backend_spec)
        if frontend_spec:
            payload["ENVIRONMENT_FRONTEND_IMAGE"] = cls.get_environment_image_ref(environment_id, frontend_spec)
        return {key: value for key, value in payload.items() if value}

    @staticmethod
    def _coerce_service_port(value: Any, *, field_name: str) -> int:
        try:
            port = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name} must be an integer")
        if port <= 0 or port > 65535:
            raise ValueError(f"{field_name} must be between 1 and 65535")
        return port

    @staticmethod
    def _normalize_compose_path(value: Any, *, field_name: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError(f"{field_name} is required")

        path = Path(raw)
        if path.is_absolute():
            raise ValueError(f"{field_name} must be a relative path")

        normalized = path.as_posix()
        parts = normalized.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"{field_name} contains invalid path traversal")
        return normalized

    def _normalize_runtime_service(
        self,
        *,
        payload: Optional[Dict[str, Any]],
        service_label: str,
        default_values: Optional[Dict[str, Any]] = None,
        require_compose_file: bool,
        required: bool,
    ) -> Optional[Dict[str, Any]]:
        if payload is None:
            if default_values is not None:
                payload = dict(default_values)
            elif required:
                raise ValueError(f"Missing required launch section: {service_label}")
            else:
                return None

        if not isinstance(payload, dict):
            raise ValueError(f"Runtime section {service_label} must be an object")

        merged: Dict[str, Any] = {}
        if default_values:
            merged.update(default_values)
        merged.update(payload)

        service_name = str(merged.get("service_name") or "").strip()
        if not service_name:
            raise ValueError(f"{service_label}.service_name is required")

        container_prefix = str(merged.get("container_prefix") or "").strip()
        if not container_prefix:
            raise ValueError(f"{service_label}.container_prefix is required")

        port = self._coerce_service_port(
            merged.get("port"),
            field_name=f"{service_label}.port",
        )

        normalized: Dict[str, Any] = {
            "service_name": service_name,
            "container_prefix": container_prefix,
            "port": port,
        }
        compose_file = merged.get("compose_file")
        if require_compose_file:
            normalized["compose_file"] = self._normalize_compose_path(
                compose_file,
                field_name=f"{service_label}.compose_file",
            )
        elif compose_file:
            normalized["compose_file"] = self._normalize_compose_path(
                compose_file,
                field_name=f"{service_label}.compose_file",
            )
        return normalized

    def _normalize_launch_images(self, environment_id: str, launch: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        raw_images = launch.get("images") if isinstance(launch.get("images"), dict) else {}
        images: Dict[str, Dict[str, Any]] = {}

        for asset_kind in ("backend", "frontend"):
            raw_spec = raw_images.get(asset_kind)
            if raw_spec is None:
                continue
            if not isinstance(raw_spec, dict):
                raise ValueError(f"launch.images.{asset_kind} must be an object")

            normalized = dict(raw_spec)
            normalized["kind"] = asset_kind
            repository = str(normalized.get("repository") or "").strip()
            if not repository:
                normalized["repository"] = self.default_environment_image_repository(environment_id, asset_kind)

            dockerfile = str(normalized.get("dockerfile") or "").strip()
            context = str(normalized.get("context") or "").strip()
            if dockerfile and os.path.isabs(dockerfile):
                raise ValueError(f"launch.images.{asset_kind}.dockerfile must be a relative path")
            if context and os.path.isabs(context):
                raise ValueError(f"launch.images.{asset_kind}.context must be a relative path")
            images[asset_kind] = normalized

        return images

    def _normalize_environment_launch(
        self,
        environment_id: str,
        launch: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(launch, dict):
            raise ValueError("launch must be an object")

        default_environment_service = {
            "compose_file": f"environments/{environment_id}/compose.run.yml",
            "service_name": "environment-backend",
            "container_prefix": "environment-backend",
            "port": 8000,
        }
        default_agent_prefix = "environment-agents"
        agent_worker_defaults = {
            "compose_file": "compose/stacks/agents.yml",
            "service_name": "agent-worker",
            "container_prefix": default_agent_prefix,
            "port": 8000,
        }

        environment_service = self._normalize_runtime_service(
            payload=launch.get("environment_service"),
            service_label="launch.environment_service",
            default_values=default_environment_service,
            require_compose_file=True,
            required=True,
        )
        if environment_service is None:
            raise ValueError("launch.environment_service section is required")

        agent_worker_service = self._normalize_runtime_service(
            payload=launch.get("agent_worker_service"),
            service_label="launch.agent_worker_service",
            default_values=agent_worker_defaults,
            require_compose_file=True,
            required=True,
        )
        if agent_worker_service is None:
            raise ValueError("launch.agent_worker_service section is required")

        frontend_service = self._normalize_runtime_service(
            payload=launch.get("frontend_service"),
            service_label="launch.frontend_service",
            require_compose_file=False,
            required=False,
        )

        images = self._normalize_launch_images(environment_id, launch)
        if frontend_service is not None and "frontend" not in images:
            raise ValueError(
                "launch.frontend_service requires launch.images.frontend with repository/context/dockerfile"
            )
        if "backend" not in images:
            raise ValueError("launch.images.backend is required")

        return {
            "environment_id": environment_id,
            "environment_service": environment_service,
            "agent_worker_service": agent_worker_service,
            "frontend_service": frontend_service,
            "images": images,
        }

    def resolve_environment_launch(self, environment_id: str) -> Dict[str, Any]:
        environment_id = str(environment_id or "").strip()
        if not environment_id:
            raise ValueError("environment_id is required")

        manifest = get_environment(environment_id)
        if manifest is None:
            raise ValueError(f"Environment not found: {environment_id}")
        launch = manifest.get("launch") if isinstance(manifest.get("launch"), dict) else {}
        return self._normalize_environment_launch(environment_id, launch)

    def list_environment_launches(self) -> Dict[str, Dict[str, Any]]:
        launches: Dict[str, Dict[str, Any]] = {}
        for manifest in discover_environments():
            environment_id = str(manifest.get("id") or "").strip()
            if not environment_id:
                continue
            try:
                launches[environment_id] = self.resolve_environment_launch(environment_id)
            except Exception as exc:
                self.logger.warning(
                    "environment launch load skipped for environment=%s: %s",
                    environment_id,
                    exc,
                )

        return launches

    def get_service_urls(
        self,
        run_id: str,
        environment_id: Optional[str] = None,
        institutional_mode: bool = False,
    ) -> Dict[str, str]:
        """Get service URLs for a run.

        Args:
            run_id: Run identifier
            environment_id: Environment identifier
            institutional_mode: Whether institutional AI is enabled

        Returns:
            Dictionary of service names to URLs
        """
        resolved_environment_id = str(environment_id or settings.get_default_environment_id()).strip()
        launch_config = self.resolve_environment_launch(resolved_environment_id)
        environment_service = launch_config["environment_service"]
        agent_service = launch_config["agent_worker_service"]

        environment_url = (
            f"http://{environment_service['container_prefix']}-{run_id}:{environment_service['port']}"
        )
        urls = {
            "environment": environment_url,
            environment_service["service_name"]: environment_url,
            "agent_worker": f"http://{agent_service['container_prefix']}-{run_id}:{agent_service['port']}",
        }

        frontend_service = launch_config.get("frontend_service")
        if frontend_service:
            urls["environment_frontend_internal"] = (
                f"http://{frontend_service['container_prefix']}-{run_id}:{frontend_service['port']}"
            )
            urls["environment_frontend"] = self.get_environment_frontend_url(run_id)

        return urls

    def generate_run_override(self, config: RunLaunchConfig, oracle_bundle_paths: Optional[Dict[str, str]] = None) -> str:
        """Generate run-specific override compose file.

        Creates per-run service naming with unique container names and network aliases
        to enable internal Docker networking communication.

        Args:
            config: Run launch configuration
            oracle_bundle_paths: Optional dict mapping Oracle bundle filenames to host paths

        Returns:
            Path to generated override file
        """
        run_id = config.run_id
        state_key = f"{config.environment_id}:{config.run_id}"
        state_path = f"/app/data/state/{config.run_id}"
        database_url = f"sqlite:////app/data/state/{config.run_id}/environment.db"
        launch_config = self.resolve_environment_launch(config.environment_id)
        environment_service = launch_config["environment_service"]
        agent_service = launch_config["agent_worker_service"]
        frontend_service = launch_config.get("frontend_service")
        frontend_port = self.get_environment_frontend_port(run_id)
        resolved_llm_provider = (
            "dummy"
            if str(config.api_key or "").strip().lower() == "dummy"
            else settings.agent_launcher_llm_provider
        )
        resolved_openrouter_key = config.api_key or settings.openrouter_api_key or ""

        # Override existing services with run-specific configuration
        # The project_name ensures isolation between runs
        override = {
            "services": {
                environment_service["service_name"]: {
                    "container_name": f"{environment_service['container_prefix']}-{run_id}",
                    "environment": {
                        "RUN_ID": config.run_id,
                        "ENV_STATE_KEY": state_key,
                        "ENV_STATE_PATH": state_path,
                        "ENV_FRONTEND_PORT": str(frontend_port),
                        "ENV_FRONTEND_URL": self.get_environment_frontend_url(config.run_id),
                        "DATABASE_URL": database_url,
                        "SEED": str(config.seed) if config.seed else "",
                        "RESOLVED_BUNDLE_HASH": config.resolved_bundle_hash or "",
                        "ENVIRONMENT_ID": config.environment_id,
                    }
                },
                agent_service["service_name"]: {
                    "container_name": f"{agent_service['container_prefix']}-{run_id}",
                    "environment": {
                        "AGENT_LAUNCHER_LLM_PROVIDER": resolved_llm_provider,
                        "RUN_ID": config.run_id,
                        "ENVIRONMENT_ID": config.environment_id,
                        "AGENT_LAUNCHER_OPENROUTER_API_KEY": resolved_openrouter_key,
                        "OPENROUTER_API_KEY": resolved_openrouter_key,
                        "AGENT_LAUNCHER_RUN_ID": config.run_id,
                    }
                }
            }
        }
        override["services"][environment_service["service_name"]]["environment"].update(
            self._legacy_runtime_alias_env(
                state_key=state_key,
                state_path=state_path,
                frontend_port=frontend_port,
                frontend_url=self.get_environment_frontend_url(config.run_id),
            )
        )

        if frontend_service:
            override["services"][frontend_service["service_name"]] = {
                "container_name": f"{frontend_service['container_prefix']}-{run_id}",
                "environment": {
                    "RUN_ID": config.run_id,
                    "BACKEND_URL": (
                        f"http://{environment_service['container_prefix']}-{run_id}:{environment_service['port']}"
                    ),
                },
            }

        # Write override file
        override_file = os.path.join(self.overrides_path, f"{config.run_id}.yml")

        with open(override_file, "w") as f:
            yaml.dump(override, f, default_flow_style=False)

        return override_file

    def _get_compose_files(self, config: RunLaunchConfig) -> List[str]:
        """Get list of compose files for a run.
        
        Args:
            config: Run configuration
            
        Returns:
            List of compose file paths relative to compose_base_path
        """
        # Note: We don't include compose/core.yml here because core services
        # (orchestrator, postgres, redis, etc.) are already running as part
        # of the platform. Per-run stacks only need the environment, agents,
        # and optional institutional AI services.
        
        launch_config = self.resolve_environment_launch(config.environment_id)
        environment_service = launch_config["environment_service"]
        agent_service = launch_config["agent_worker_service"]
        files = [
            environment_service["compose_file"],
            agent_service["compose_file"],
        ]
        frontend_service = launch_config.get("frontend_service")
        if isinstance(frontend_service, dict):
            frontend_compose = str(frontend_service.get("compose_file") or "").strip()
            if frontend_compose:
                files.append(frontend_compose)

        # Add override file
        override_file = os.path.join(self.overrides_path, f"{config.run_id}.yml")
        if os.path.exists(override_file):
            files.append(f"compose/stacks/run-overrides/{config.run_id}.yml")

        deduped: List[str] = []
        seen = set()
        for item in files:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)

        return deduped

    async def check_service_health(
        self,
        run_id: str,
        service_name: str,
        health_url: str,
        timeout: int = 120,
        interval: float = 2.0,
        max_retries: int = 60
    ) -> Dict:
        """Check health of a per-run service with polling.

        Polls the service health endpoint until it returns success
        or timeout/max retries is reached.

        Args:
            run_id: Run identifier
            service_name: Name of the service (for logging)
            health_url: URL to the health endpoint
            timeout: Total timeout in seconds
            interval: Polling interval in seconds
            max_retries: Maximum number of retry attempts

        Returns:
            Health check result with status and details
        """
        start_time = time.time()
        retries = 0

        async with httpx.AsyncClient(timeout=10.0) as client:
            while retries < max_retries:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    return {
                        "status": "timeout",
                        "run_id": run_id,
                        "service": service_name,
                        "health_url": health_url,
                        "elapsed_seconds": elapsed,
                        "retries": retries,
                        "error": f"Health check timed out after {timeout}s"
                    }

                try:
                    response = await client.get(f"{health_url}/health")
                    if response.status_code == 200:
                        return {
                            "status": "healthy",
                            "run_id": run_id,
                            "service": service_name,
                            "health_url": health_url,
                            "elapsed_seconds": elapsed,
                            "retries": retries,
                            "response": response.json() if response.text else {}
                        }
                except httpx.ConnectError:
                    # Service not yet available, wait and retry
                    pass
                except Exception as e:
                    # Other errors, log but continue polling
                    pass

                retries += 1
                await asyncio.sleep(interval)

        return {
            "status": "unhealthy",
            "run_id": run_id,
            "service": service_name,
            "health_url": health_url,
            "elapsed_seconds": time.time() - start_time,
            "retries": retries,
            "error": "Max retries exceeded"
        }

    async def wait_for_run_services(
        self,
        run_id: str,
        environment_id: Optional[str] = None,
        institutional_mode: bool = False,
        timeout: int = 120,
        interval: float = 2.0
    ) -> Dict:
        """Wait for all per-run services to be healthy.

        Polls each service's health endpoint until all are healthy
        or timeout is reached.

        Args:
            run_id: Run identifier
            environment_id: Environment identifier
            institutional_mode: Whether institutional AI is enabled
            timeout: Timeout in seconds for all services
            interval: Polling interval in seconds

        Returns:
            Health check results for all services
        """
        resolved_environment_id = str(environment_id or settings.get_default_environment_id()).strip()
        service_urls = self.get_service_urls(run_id, resolved_environment_id, institutional_mode)
        results = {}
        launch_config = self.resolve_environment_launch(resolved_environment_id)

        # Use the declared environment service as the primary health gate.
        service_name = launch_config["environment_service"]["service_name"]

        # Check environment service first (primary service)
        env_url = service_urls["environment"]
        results[service_name] = await self.check_service_health(
            run_id=run_id,
            service_name=service_name,
            health_url=env_url,
            timeout=timeout,
            interval=interval
        )

        if results[service_name]["status"] != "healthy":
            return {
                "status": "failed",
                "run_id": run_id,
                "services": results,
                "error": f"{service_name} service failed health check"
            }

        return {
            "status": "healthy",
            "run_id": run_id,
            "services": results,
            "service_urls": service_urls
        }

    def launch_run(self, config: RunLaunchConfig) -> Dict:
        """Launch a run via the orchestrator service.

        Args:
            config: Run launch configuration

        Returns:
            Launch result with status and details
        """
        try:
            launch_config = self.resolve_environment_launch(config.environment_id)

            # Generate override file with per-run service naming
            override_file = self.generate_run_override(config, None)
            
            # Get compose files
            compose_files = self._get_compose_files(config)
            
            # Prepare environment variables
            state_key = f"{config.environment_id}:{config.run_id}"
            state_path = f"/app/data/state/{config.run_id}"
            database_url = f"sqlite:////app/data/state/{config.run_id}/environment.db"
            frontend_port = self.get_environment_frontend_port(config.run_id)
            resolved_llm_provider = (
                "dummy"
                if str(config.api_key or "").strip().lower() == "dummy"
                else settings.agent_launcher_llm_provider
            )
            resolved_openrouter_key = config.api_key or settings.openrouter_api_key or ""
            env_vars = {
                "RUN_ID": config.run_id,
                "HOST_PROJECT_ROOT": os.getenv("HOST_PROJECT_ROOT", "/app"),
                "MASE_NETWORK_NAME": os.getenv("MASE_NETWORK_NAME", "mase-network"),
                "ENV_STATE_KEY": state_key,
                "ENV_STATE_PATH": state_path,
                "DATABASE_URL": database_url,
                "ENV_FRONTEND_PORT": str(frontend_port),
                "ENV_FRONTEND_URL": self.get_environment_frontend_url(config.run_id),
                "ENV_CONTAINER_PREFIX": config.environment_id,
                "ENVIRONMENT_FRONTEND_PORT": str(frontend_port),
                "ENVIRONMENT_FRONTEND_URL": self.get_environment_frontend_url(config.run_id),
                "ENVIRONMENT_NAME": config.environment_id,
                "SEED": str(config.seed) if config.seed else "",
                "RESOLVED_BUNDLE_HASH": config.resolved_bundle_hash or "",
                "ENVIRONMENT_ID": config.environment_id,
                "AGENT_LAUNCHER_LLM_PROVIDER": resolved_llm_provider,
                "AGENT_LAUNCHER_OPENROUTER_API_KEY": resolved_openrouter_key,
                "OPENROUTER_API_KEY": resolved_openrouter_key,
            }
            env_vars.update(self.get_run_image_env_vars(config.environment_id, launch_config))
            env_vars.update(
                self._legacy_runtime_alias_env(
                    state_key=state_key,
                    state_path=state_path,
                    frontend_port=frontend_port,
                    frontend_url=self.get_environment_frontend_url(config.run_id),
                )
            )
            
            # Call orchestrator to start run stack
            import httpx
            response = httpx.post(
                f"{self.orchestrator_url}/orchestrator/runs/{config.run_id}/start",
                json={
                    "compose_files": compose_files,
                    "env_vars": env_vars
                },
                timeout=120.0
            )
            
            if response.status_code != 200:
                return {
                    "status": "failed",
                    "run_id": config.run_id,
                    "error": f"Orchestrator returned {response.status_code}: {response.text}",
                    "stage": "orchestrator_call"
                }
            
            result = response.json()
            service_urls = self.get_service_urls(
                config.run_id,
                config.environment_id,
                config.institutional_mode,
            )
            
            launch_result = {
                "status": "launched",
                "run_id": config.run_id,
                "institutional_mode": config.institutional_mode,
                "override_file": override_file,
                "orchestrator_response": result,
                "launched_at": datetime.utcnow().isoformat(),
                "service_urls": service_urls,
                "environment_launch": launch_config,
            }
            return launch_result

        except Exception as e:
            return {
                "status": "error",
                "run_id": config.run_id,
                "error": str(e),
                "stage": "launch"
            }

    def stop_run(self, run_id: str, institutional_mode: bool = False) -> Dict:
        """Stop a run via the orchestrator service.

        Args:
            run_id: Run identifier
            institutional_mode: Whether institutional AI was enabled

        Returns:
            Stop result
        """
        try:
            # Call orchestrator to stop run stack
            import httpx
            response = httpx.post(
                f"{self.orchestrator_url}/orchestrator/runs/{run_id}/stop",
                timeout=60.0
            )
            
            if response.status_code != 200:
                return {
                    "status": "failed",
                    "run_id": run_id,
                    "error": f"Orchestrator returned {response.status_code}: {response.text}",
                }
            
            result = response.json()
            
            # Clean up run override artifacts.
            self._cleanup_override_artifacts(run_id)

            return {
                "status": "success",
                "run_id": run_id,
                "stopped_at": datetime.utcnow().isoformat(),
                "orchestrator_response": result,
            }

        except Exception as e:
            return {
                "status": "error",
                "run_id": run_id,
                "error": str(e),
                "stage": "stop"
            }

    def delete_run(self, run_id: str, institutional_mode: bool = False) -> Dict:
        """Delete a run via the orchestrator service.

        This stops and removes containers (unlike stop_run which only stops them).

        Args:
            run_id: Run identifier
            institutional_mode: Whether institutional AI was enabled

        Returns:
            Delete result
        """
        try:
            # Call orchestrator to delete run stack (DELETE endpoint)
            import httpx
            response = httpx.delete(
                f"{self.orchestrator_url}/orchestrator/runs/{run_id}",
                timeout=60.0
            )

            if response.status_code != 200:
                return {
                    "status": "failed",
                    "run_id": run_id,
                    "error": f"Orchestrator returned {response.status_code}: {response.text}",
                }

            result = response.json()

            # Clean up run override artifacts.
            self._cleanup_override_artifacts(run_id)

            return {
                "status": "success",
                "run_id": run_id,
                "deleted_at": datetime.utcnow().isoformat(),
                "orchestrator_response": result,
            }

        except Exception as e:
            return {
                "status": "error",
                "run_id": run_id,
                "error": str(e),
                "stage": "delete"
            }

    def get_run_status(self, run_id: str) -> Dict:
        """Get status of a running run stack.

        Args:
            run_id: Run identifier

        Returns:
            Status information
        """
        try:
            # Call orchestrator to get run status
            import httpx
            response = httpx.get(
                f"{self.orchestrator_url}/orchestrator/runs/{run_id}/status",
                timeout=10.0
            )
            
            if response.status_code == 404:
                return {
                    "run_id": run_id,
                    "status": "not_found",
                    "message": "Run not found in orchestrator"
                }
            
            if response.status_code != 200:
                return {
                    "run_id": run_id,
                    "status": "error",
                    "error": f"Orchestrator returned {response.status_code}: {response.text}",
                }
            
            return response.json()

        except Exception as e:
            return {
                "run_id": run_id,
                "status": "error",
                "error": str(e),
            }


# Global launcher instance
run_launcher = RunLauncher()
