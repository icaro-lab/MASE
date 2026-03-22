"""Configuration for the Environment Orchestrator service."""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """Orchestrator configuration settings."""
    
    # Service settings
    service_name: str = "orchestrator"
    version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8003
    
    # Docker settings
    docker_socket_path: str = "/var/run/docker.sock"
    docker_host: Optional[str] = None
    
    # Health check settings
    health_check_timeout: int = Field(default=300, description="Health check timeout in seconds")
    health_check_interval: int = Field(default=5, description="Health check interval in seconds")
    health_check_retries: int = Field(default=60, description="Number of health check retries")
    
    # Resource limits
    default_cpu_limit: float = Field(default=2.0, description="Default CPU limit per environment")
    default_memory_limit: str = Field(default="2g", description="Default memory limit per environment")
    
    # Network settings
    default_network: str = Field(default="mase-network", description="Default Docker network")
    
    # Security settings
    allowed_compose_dirs: str = Field(
        default="./environments,/app/environments",
        description="Comma-separated list of allowed directories for compose files"
    )
    prevent_directory_traversal: bool = True
    
    # Host path settings (for Docker-in-Docker scenarios)
    host_project_root: str = Field(
        default=".",
        description="Host path to project root (used when running in container)"
    )
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    class Config:
        env_prefix = "ORCHESTRATOR_"
        case_sensitive = False


# Global settings instance
settings = Settings()
