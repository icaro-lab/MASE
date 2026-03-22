"""Configuration for the Agent Launcher service."""

import os
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # LLM Configuration
    llm_provider: str = "openrouter"  # openrouter, openai, anthropic
    llm_model: str = "openai/gpt-5-mini"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_timeout: int = 60
    
    # API Keys
    openrouter_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    
    # OpenRouter Configuration
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_http_referer: Optional[str] = None  # Site URL for OpenRouter rankings
    openrouter_x_title: Optional[str] = None  # App name for OpenRouter rankings
    
    # Agent Filesystem
    agents_base_path: str = "/agents"
    
    # Execution Limits
    max_actions_per_heartbeat: int = 10
    http_timeout: int = 30
    
    # Service Configuration
    environment_url: Optional[str] = None
    service_port: int = 8000  # Port for self-referential calls
    run_id: Optional[str] = None  # Current run ID for runtime scoping
    
    # Scheduler Configuration (with env var fallbacks)
    heartbeat_interval: str = "5m"
    heartbeat_timeout: str = "2m"
    max_parallel_agents: int = 10

    @model_validator(mode="after")
    def apply_openrouter_key_fallbacks(self) -> "Settings":
        if self.openrouter_api_key:
            return self
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY") or None
        return self
    
    model_config = SettingsConfigDict(
        env_prefix="AGENT_LAUNCHER_",
        env_file=".env",
    )


# Global settings instance
settings = Settings()
