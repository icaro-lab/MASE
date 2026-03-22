from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


app = FastAPI(title="Template Environment Backend", version="0.1.0")
SKILL_MD_PATH = Path("/app/skill.md")
REGISTERED_TOKENS: dict[str, dict[str, str]] = {}
FEED = [
    {
        "id": "welcome-post",
        "title": "Welcome to Template Environment",
        "body": "Replace this backend with your real environment logic.",
    }
]


class RegisterRequest(BaseModel):
    agent_id: str
    name: str | None = None


def _require_token(authorization: str | None) -> dict[str, str]:
    token = str(authorization or "").removeprefix("Bearer ").strip()
    payload = REGISTERED_TOKENS.get(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Unknown agent token")
    return payload


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/contract")
async def contract() -> dict:
    return {
        "contract_version": "v1.0.0",
        "environment_name": "Template Environment",
        "environment_version": "0.1.0",
        "capabilities": [
            "health_check",
            "contract_discovery",
            "skill_documentation",
            "action_api",
        ],
        "event_schema_version": "v1",
        "description": "Minimal environment template",
        "endpoints": [
            {"path": "/health", "method": "GET", "description": "Health check"},
            {"path": "/contract", "method": "GET", "description": "Environment contract"},
            {"path": "/skill.md", "method": "GET", "description": "Runtime bootstrap skill document"},
            {"path": "/auth/register", "method": "POST", "description": "Register one runtime agent"},
            {"path": "/api/v1/feed", "method": "GET", "description": "Read visible feed items"},
        ],
    }


@app.get("/skill.md", response_model=None)
async def skill_md() -> str:
    return SKILL_MD_PATH.read_text(encoding="utf-8")


@app.post("/auth/register")
async def register_agent(request: RegisterRequest) -> dict[str, str]:
    token = f"token-{uuid4()}"
    REGISTERED_TOKENS[token] = {
        "agent_id": request.agent_id,
        "name": request.name or request.agent_id,
    }
    return {
        "agent_id": request.agent_id,
        "api_token": token,
    }


@app.get("/api/v1/feed")
async def get_feed(authorization: str | None = Header(default=None)) -> dict:
    _require_token(authorization)
    return {
        "items": FEED,
        "count": len(FEED),
    }
