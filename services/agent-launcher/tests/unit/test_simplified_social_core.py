"""Unit tests for simplified ideology runtime helpers."""

import json
from pathlib import Path

import pytest

from app.action_parser import parse_multiple_actions
from app.agent_fs import AgentFilesystem
from app.config import settings
from app.routes import launcher as launcher_module
from app.simplified_social_core import (
    credentials_filename_for_environment,
    SIMPLIFIED_FEED_VOTE_LLM_TOOLS,
    SIMPLIFIED_SOCIAL_LLM_TOOLS,
)


@pytest.mark.unit
def test_simplified_tool_surface_is_minimal() -> None:
    names = [item["function"]["name"] for item in SIMPLIFIED_SOCIAL_LLM_TOOLS]
    assert names == [
        "register_on_environment",
        "get_feed",
        "get_post",
        "get_post_comments",
        "create_comment",
        "upvote_post",
        "downvote_post",
        "heartbeat_ok",
    ]


@pytest.mark.unit
def test_feed_vote_tool_surface_is_vote_only() -> None:
    names = [item["function"]["name"] for item in SIMPLIFIED_FEED_VOTE_LLM_TOOLS]
    assert names == [
        "get_feed",
        "upvote_post",
        "downvote_post",
        "heartbeat_ok",
    ]


@pytest.mark.unit
def test_action_parser_maps_simplified_tool_calls() -> None:
    actions = parse_multiple_actions(
        'get_feed({"limit":15,"sort":"new"})\n'
        'get_post({"post_id":"post-1"})\n'
        'create_comment({"post_id":"post-1","content":"hello"})\n'
        'upvote_post({"post_id":"post-1"})\n'
        'downvote_post({"post_id":"post-2"})\n'
        'heartbeat_ok({})'
    )
    assert [action.action_name or action.action.value for action in actions[:5]] == [
        "get_feed",
        "get_post",
        "create_comment",
        "upvote_post",
        "downvote_post",
    ]
    assert actions[0].url == "/api/v1/feed?sort=new&limit=15"
    assert actions[1].url == "/api/v1/posts/post-1"
    assert actions[2].url == "/api/v1/posts/post-1/comments"
    assert actions[3].url == "/api/v1/posts/post-1/upvote"
    assert actions[4].url == "/api/v1/posts/post-2/downvote"
    assert actions[5].action.value == "heartbeat_ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_launcher_auth_seed_falls_back_to_workspace_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(settings, "agents_base_path", str(agents_root))

    fs = AgentFilesystem("agent-1")
    fs.agent_path.mkdir(parents=True, exist_ok=True)
    fs.workspace_path.mkdir(parents=True, exist_ok=True)
    fs.skills_path.mkdir(parents=True, exist_ok=True)
    await fs.write_file(
        credentials_filename_for_environment("moltbook"),
        json.dumps({"api_token": "workspace-token"}),
    )

    seed = await launcher_module._load_environment_auth_registry_seed(
        fs,
        environment_name="moltbook",
        environment_url="http://example-env:8000",
    )
    assert seed == {
        "entries": {
            "http://example-env:8000": {
                "api_token": "workspace-token",
                "source": "workspace_credentials",
            }
        }
    }
