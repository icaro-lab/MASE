from __future__ import annotations

from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="Hello World Backend", version="0.1.0")
SKILL_MD_PATH = Path("/app/skill.md")
TOKEN_LOCK = Lock()
BOARD_LOCK = Lock()
REGISTERED_TOKENS: dict[str, dict[str, str]] = {}
BOARD_NOTES: list[dict[str, str]] = [
    {
        "id": "starter-1",
        "author": "Caretaker",
        "text": "Welcome to the whiteboard. Leave one short funny note.",
    },
    {
        "id": "starter-2",
        "author": "Caretaker",
        "text": "Keep it short enough to fit in chalk.",
    },
]


class RegisterRequest(BaseModel):
    agent_id: str
    name: str | None = None


class BatchRegisterRequest(BaseModel):
    agents: list[RegisterRequest]


class NoteCreateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=140)


def _require_token(authorization: str | None) -> dict[str, str]:
    token = str(authorization or "").removeprefix("Bearer ").strip()
    with TOKEN_LOCK:
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
        "environment_name": "Hello World",
        "environment_version": "0.1.0",
        "capabilities": [
            "health_check",
            "contract_discovery",
            "skill_documentation",
            "action_api",
        ],
        "description": "A tiny shared whiteboard environment for demonstrating the minimum MASE environment contract.",
        "endpoints": [
            {"path": "/health", "method": "GET", "description": "Health check"},
            {"path": "/contract", "method": "GET", "description": "Environment contract"},
            {"path": "/skill.md", "method": "GET", "description": "Runtime bootstrap skill document"},
            {"path": "/auth/register", "method": "POST", "description": "Register one runtime agent"},
            {"path": "/api/v1/board", "method": "GET", "description": "Read the current whiteboard"},
            {"path": "/api/v1/board/notes", "method": "POST", "description": "Add one short note to the board"},
        ],
    }


@app.get("/skill.md", response_model=None)
async def skill_md() -> str:
    return SKILL_MD_PATH.read_text(encoding="utf-8")


@app.post("/auth/register")
async def register_agent(request: RegisterRequest) -> dict[str, str]:
    token = f"token-{uuid4()}"
    payload = {
        "agent_id": request.agent_id,
        "name": request.name or request.agent_id,
    }
    with TOKEN_LOCK:
        REGISTERED_TOKENS[token] = payload
    return {
        "agent_id": request.agent_id,
        "api_token": token,
    }


@app.post("/auth/register/batch")
async def register_agents(request: BatchRegisterRequest) -> dict[str, list[dict[str, str]]]:
    results = [await register_agent(agent) for agent in request.agents]
    return {"results": results}


@app.get("/api/v1/board")
async def get_board() -> dict:
    with BOARD_LOCK:
        notes = list(BOARD_NOTES)
    return {
        "title": "Hello World Whiteboard",
        "prompt": "Leave one short, playful note.",
        "notes": notes,
        "count": len(notes),
    }


@app.post("/api/v1/board/notes")
async def add_note(
    request: NoteCreateRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    actor = _require_token(authorization)
    note_text = request.text.strip()
    if not note_text:
        raise HTTPException(status_code=400, detail="Note text cannot be blank")
    with BOARD_LOCK:
        if len(BOARD_NOTES) >= 32:
            raise HTTPException(status_code=409, detail="Whiteboard is full")
        note = {
            "id": f"note-{uuid4()}",
            "author": actor["name"],
            "text": note_text,
        }
        BOARD_NOTES.append(note)
        notes = list(BOARD_NOTES)
    return {
        "ok": True,
        "note": note,
        "count": len(notes),
    }
