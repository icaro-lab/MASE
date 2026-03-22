"""Configuration settings for the admin dashboard."""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Application
    app_name: str = "MASE Admin Dashboard"
    debug: bool = False
    
    # Paths
    # Service URLs
    orchestrator_url: str = "http://orchestrator:8006"
    controller_url: str = "http://controller:8002"
    
    # CORS
    cors_origins: list[str] = [
        "http://localhost:3016",
        "http://localhost:3000",
        "http://localhost:8080",
    ]
    
    # Timeouts
    http_timeout: float = 30.0

    # OpenRouter key fallback configuration
    openrouter_api_key_fallback: str = ""
    openrouter_api_key_file: str = "/data/admin-openrouter-settings.json"

    class Config:
        env_prefix = "ADMIN_"
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
