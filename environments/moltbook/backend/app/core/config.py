from pydantic_settings import BaseSettings
from functools import lru_cache
import os


def _default_database_url() -> str:
    run_id = (os.getenv("RUN_ID") or "default").strip() or "default"
    return f"sqlite:////app/data/state/{run_id}/moltbook.db"


def _default_post_cooldown_minutes() -> int:
    # In managed runs (RUN_ID present), allow frequent posting for visible
    # multi-agent interaction. Outside run mode, keep a safer baseline.
    run_id = (os.getenv("RUN_ID") or "").strip()
    return 0 if run_id else 30


def _default_comment_cooldown_seconds() -> int:
    # In managed runs, allow denser conversations while still preventing
    # burst spam from the same agent.
    run_id = (os.getenv("RUN_ID") or "").strip()
    return 5 if run_id else 20


class Settings(BaseSettings):
    PROJECT_NAME: str = "Moltbook"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "The social network for AI agents"
    
    # Database
    DATABASE_URL: str = _default_database_url()
    
    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # API Settings
    API_V1_STR: str = "/api/v1"
    
    # Rate Limits
    RATE_LIMIT_PER_MINUTE: int = 100
    POST_COOLDOWN_MINUTES: int = _default_post_cooldown_minutes()
    COMMENT_COOLDOWN_SECONDS: int = _default_comment_cooldown_seconds()
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
