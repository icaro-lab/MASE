from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app


client = TestClient(app)


def test_contract_lists_board_endpoints() -> None:
    response = client.get("/contract")
    assert response.status_code == 200
    payload = response.json()
    endpoints = {(item["method"], item["path"]) for item in payload["endpoints"]}
    assert ("GET", "/api/v1/board") in endpoints
    assert ("POST", "/api/v1/board/notes") in endpoints
    assert ("POST", "/run/init") in endpoints
    assert ("POST", "/run/reset") in endpoints


def test_register_and_add_note() -> None:
    register = client.post(
        "/auth/register",
        json={"agent_id": "agent-1", "name": "Chalk Bot 1"},
    )
    assert register.status_code == 200
    token = register.json()["api_token"]

    note_response = client.post(
        "/api/v1/board/notes",
        json={"text": "A tiny robot drew a sun in the corner."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert note_response.status_code == 200
    payload = note_response.json()
    assert payload["ok"] is True
    assert payload["note"]["author"] == "Chalk Bot 1"

    board = client.get("/api/v1/board")
    assert board.status_code == 200
    assert any(note["author"] == "Chalk Bot 1" for note in board.json()["notes"])

    duplicate = client.post(
        "/api/v1/board/notes",
        json={"text": "This second note should be rejected."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Agent already posted a note"


def test_batch_register_returns_tokens() -> None:
    response = client.post(
        "/auth/register/batch",
        json={
            "agents": [
                {"agent_id": "agent-2", "name": "Chalk Bot 2"},
                {"agent_id": "agent-3", "name": "Chalk Bot 3"},
            ]
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 2
    assert all(item["api_token"] for item in payload["results"])


def test_run_init_is_idempotent() -> None:
    first = client.post(
        "/run/init",
        json={
            "run_id": "run-hello",
            "environment_id": "hello-world",
            "params": {"mood": "playful"},
            "assignment_map": {"agent-1": {"role": "chalk-bot"}},
        },
    )
    assert first.status_code == 200
    assert first.json()["idempotent"] is False

    second = client.post(
        "/run/init",
        json={
            "run_id": "run-hello",
            "environment_id": "hello-world",
            "params": {"mood": "playful"},
        },
    )
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
