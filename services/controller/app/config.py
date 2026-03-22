"""Configuration management for the run controller."""
import os
from typing import Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # Service configuration
    service_name: str = Field(default="controller")
    debug: bool = Field(default=False)

    # Server configuration
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # External service URLs
    agent_launcher_url: str = Field(default="http://agent-launcher:8000")
    environment_url: str = Field(default="http://environment:8000")
    orchestrator_url: str = Field(default="http://orchestrator:8006")
    default_environment_id: str = Field(default="")

    # Per-run service URL patterns (dynamic naming)
    environment_url_pattern: str = Field(default="http://{environment_id}-backend-{run_id}:8000")
    agent_worker_url_pattern: str = Field(default="http://{environment_id}-agents-{run_id}:8000")

    # Health check configuration for per-run services
    health_check_timeout: int = Field(default=120)
    health_check_interval: float = Field(default=2.0)
    health_check_max_retries: int = Field(default=60)

    # Data paths
    data_dir: str = Field(default="/data")
    metrics_path: str = Field(default="/data/metrics")
    state_path: str = Field(default="/data/state")
    packages_path: str = Field(default="/packages")
    compose_base_path: str = Field(default="./compose")
    # Optional legacy compatibility alias env var names.
    # Keep empty by default to avoid environment-specific hardcoding.
    legacy_state_key_env_var: str = Field(default="")
    legacy_state_path_env_var: str = Field(default="")
    legacy_frontend_port_env_var: str = Field(default="")
    legacy_frontend_url_env_var: str = Field(default="")

    # Database
    database_url: str = Field(default="sqlite:////data/run_controller.db")

    # API Keys
    openrouter_api_key: str = Field(default="")
    agent_launcher_llm_provider: str = Field(default="openrouter")

    @model_validator(mode="after")
    def apply_openrouter_key_fallbacks(self) -> "Settings":
        if self.openrouter_api_key:
            return self
        self.openrouter_api_key = (
            os.getenv("OPENROUTER_API_KEY")
            or os.getenv("AGENT_LAUNCHER_OPENROUTER_API_KEY")
            or ""
        )
        return self

    def _normalize_environment_id(self, environment_id: Optional[str]) -> Optional[str]:
        raw = str(environment_id or "").strip().strip("/")
        if not raw:
            return None
        if raw.startswith("environments/"):
            raw = raw[len("environments/") :]
        if raw.startswith("environment/"):
            raw = raw[len("environment/") :]
        normalized = raw.split("/")[-1] if raw else ""
        if "@" in normalized:
            normalized = normalized.split("@", 1)[0]
        return normalized or None

    def _candidate_environment_roots(self) -> list[str]:
        roots: list[str] = []
        configured = str(self.packages_path or "").strip()
        if configured:
            roots.append(configured)
            roots.append(os.path.join(configured, "environments"))
            configured_abs = os.path.abspath(configured)
            if configured_abs not in {"/packages"}:
                deduped: list[str] = []
                seen = set()
                for root in roots:
                    if root in seen:
                        continue
                    seen.add(root)
                    deduped.append(root)
                return deduped
        roots.append("./environments")
        roots.append("/app/environments")
        deduped: list[str] = []
        seen = set()
        for root in roots:
            if root in seen:
                continue
            seen.add(root)
            deduped.append(root)
        return deduped

    def _discover_default_environment_id(self) -> Optional[str]:
        discovered: set[str] = set()
        for root in self._candidate_environment_roots():
            if not os.path.isdir(root):
                continue
            try:
                for entry in os.listdir(root):
                    entry_path = os.path.join(root, entry)
                    if not os.path.isdir(entry_path):
                        continue
                    manifest_path = os.path.join(entry_path, "environment.yaml")
                    if os.path.isfile(manifest_path):
                        discovered.add(entry)
            except OSError:
                continue
        if not discovered:
            return None
        if len(discovered) == 1:
            return next(iter(discovered))
        discovered_list = ", ".join(sorted(discovered))
        raise ValueError(
            "Multiple environments discovered without an explicit default: "
            f"{discovered_list}. Set SIM_CTRL_DEFAULT_ENVIRONMENT_ID."
        )

    def get_default_environment_id(self) -> str:
        """Get normalized default environment identifier."""
        configured = self._normalize_environment_id(self.default_environment_id)
        if configured:
            return configured

        discovered = self._discover_default_environment_id()
        if discovered:
            return discovered

        raise ValueError(
            "No default environment configured or discovered. "
            "Set SIM_CTRL_DEFAULT_ENVIRONMENT_ID or provide exactly one environment manifest."
        )

    def get_environment_url(self, run_id: str, environment_id: Optional[str] = None) -> str:
        """Get environment URL for a specific run.

        Args:
            run_id: The run identifier
            environment_id: Optional environment identifier

        Returns:
            URL to the environment service for this run
        """
        resolved_environment = (
            self._normalize_environment_id(environment_id) or self.get_default_environment_id()
        )
        return self.environment_url_pattern.format(run_id=run_id, environment_id=resolved_environment)

    def get_agent_worker_url(self, run_id: str, environment_id: Optional[str] = None) -> str:
        """Get agent worker URL for a specific run.

        Args:
            run_id: The run identifier
            environment_id: Optional environment identifier

        Returns:
            URL to the agent worker service for this run
        """
        resolved_environment = (
            self._normalize_environment_id(environment_id) or self.get_default_environment_id()
        )
        return self.agent_worker_url_pattern.format(run_id=run_id, environment_id=resolved_environment)
    
    @field_validator("metrics_path", "state_path", "packages_path")
    @classmethod
    def create_absolute_path(cls, v: str) -> str:
        """Ensure paths are absolute."""
        if not os.path.isabs(v):
            return os.path.abspath(v)
        return v
    
    class Config:
        env_prefix = "SIM_CTRL_"
        case_sensitive = False


# Global settings instance
settings = Settings()


def load_yaml_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    import yaml
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    return config.get("run", config)


def merge_config(base: dict, override: dict) -> dict:
    """Merge override config into base config."""
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = value
    
    return result
