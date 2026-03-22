from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
import types
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _DummyJWTError(Exception):
    pass


class _DummyCryptContext:
    def __init__(self, *args, **kwargs):
        pass

    def hash(self, password: str) -> str:
        return f"hash::{password}"

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        return hashed_password in {plain_password, f"hash::{plain_password}"}


sys.modules.setdefault(
    "jose",
    types.SimpleNamespace(
        JWTError=_DummyJWTError,
        jwt=types.SimpleNamespace(
            encode=lambda *args, **kwargs: "dummy-token",
            decode=lambda *args, **kwargs: {},
        ),
    ),
)
sys.modules.setdefault(
    "passlib",
    types.SimpleNamespace(context=types.SimpleNamespace(CryptContext=_DummyCryptContext)),
)
sys.modules.setdefault(
    "passlib.context",
    types.SimpleNamespace(CryptContext=_DummyCryptContext),
)

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent import Agent
from app.models.comment import Comment
from app.models.post import Post
from app.models.run_policy_state import RunPolicyState
from app.models.submolt import Submolt
from app.models.vote import Vote, VoteType


def _load_module(module_name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


feed = _load_module("feed_module_test", "app/api/feed.py")
posts = _load_module("posts_module_test", "app/api/posts.py")
votes = _load_module("votes_module_test", "app/api/votes.py")


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def api_client(db_session):
    app = FastAPI()
    app.include_router(feed.router, prefix="/feed")
    app.include_router(posts.router, prefix="/posts")
    session_factory = sessionmaker(bind=db_session.bind, autocommit=False, autoflush=False)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[feed.get_db] = _override_get_db
    app.dependency_overrides[posts.get_db] = _override_get_db
    app.dependency_overrides[feed.get_current_agent_optional] = lambda: None
    app.dependency_overrides[posts.get_current_agent_optional] = lambda: None

    with TestClient(app) as client:
        yield client


@pytest.fixture
def authenticated_api_client(db_session):
    app = FastAPI()
    app.include_router(feed.router, prefix="/feed")
    app.include_router(votes.router, prefix="/votes")
    session_factory = sessionmaker(bind=db_session.bind, autocommit=False, autoflush=False)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    viewer = Agent(
        id="viewer-1",
        name="Viewer",
        api_key="token-viewer",
        verification_code="verify-viewer",
        is_claimed=True,
        is_active=True,
    )
    db_session.add(viewer)
    db_session.commit()

    app.dependency_overrides[feed.get_db] = _override_get_db
    app.dependency_overrides[votes.get_db] = _override_get_db
    app.dependency_overrides[feed.get_current_agent_optional] = lambda: viewer
    app.dependency_overrides[votes.get_current_agent] = lambda: viewer

    with TestClient(app) as client:
        yield client


def _seed_post(db_session, *, upvotes: int, downvotes: int) -> Post:
    author = Agent(
        id="author-1",
        name="Wire Desk",
        api_key="token-author",
        verification_code="verify-author",
        is_claimed=True,
        is_active=True,
    )
    submolt = Submolt(
        id="submolt-general",
        name="general",
        display_name="General",
        owner_id=author.id,
    )
    post = Post(
        id=str(uuid.uuid4()),
        title="Test title",
        content="Short snippet for the feed card.",
        author_id=author.id,
        submolt_id=submolt.id,
        upvotes=upvotes,
        downvotes=downvotes,
        hot_score=0.0,
    )
    comment = Comment(
        id=str(uuid.uuid4()),
        content="comment",
        author_id=author.id,
        post_id=post.id,
    )
    db_session.add(author)
    db_session.add(submolt)
    db_session.add(post)
    db_session.add(comment)
    db_session.commit()
    db_session.refresh(post)
    return post


def _seed_hidden_policy(db_session, *, run_id: str = "run-hidden") -> None:
    state = RunPolicyState(
        run_id=run_id,
        policy_json={
            "env": {
                "moltbook": {
                    "feed": {
                        "vote_visibility": "hidden",
                        "hide_comment_counts": True,
                    }
                }
            }
        },
    )
    db_session.add(state)
    db_session.commit()


def test_feed_returns_hidden_vote_counts_when_policy_requests_it(api_client, db_session) -> None:
    _seed_post(db_session, upvotes=7, downvotes=2)
    _seed_hidden_policy(db_session)

    response = api_client.get("/feed?sort=new&limit=10", headers={"x-run-id": "run-hidden"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["upvotes"] == 0
    assert payload[0]["downvotes"] == 0


def test_feed_keeps_real_vote_counts_for_frontend_when_policy_hides_only_for_agents(api_client, db_session) -> None:
    _seed_post(db_session, upvotes=7, downvotes=2)
    state = RunPolicyState(
        run_id="run-agent-hidden",
        policy_json={
            "env": {
                "moltbook": {
                    "feed": {
                        "vote_visibility": "agent_hidden",
                        "hide_comment_counts": True,
                    }
                }
            }
        },
    )
    db_session.add(state)
    db_session.commit()

    response = api_client.get("/feed?sort=new&limit=10", headers={"x-run-id": "run-agent-hidden"})
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["upvotes"] == 7
    assert payload[0]["downvotes"] == 2


def test_feed_hides_vote_counts_for_agent_context_when_policy_requests_agent_hidden(authenticated_api_client, db_session) -> None:
    _seed_post(db_session, upvotes=7, downvotes=2)
    state = RunPolicyState(
        run_id="run-agent-hidden",
        policy_json={
            "env": {
                "moltbook": {
                    "feed": {
                        "vote_visibility": "agent_hidden",
                        "hide_comment_counts": True,
                    }
                }
            }
        },
    )
    db_session.add(state)
    db_session.commit()

    response = authenticated_api_client.get(
        "/feed?sort=new&limit=10",
        headers={"x-run-id": "run-agent-hidden", "x-agent-id": "viewer-1", "Authorization": "Bearer token-viewer"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["upvotes"] == 0
    assert payload[0]["downvotes"] == 0
    assert payload[0]["score"] == 0
    assert payload[0]["comment_count"] == 0


def test_posts_list_keeps_vote_counts_visible_without_policy(api_client, db_session) -> None:
    _seed_post(db_session, upvotes=4, downvotes=1)

    response = api_client.get("/posts?sort=new&limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["upvotes"] == 4
    assert payload[0]["downvotes"] == 1
    assert payload[0]["score"] == 3


def test_duplicate_vote_does_not_toggle_when_one_vote_policy_is_enabled(
    authenticated_api_client,
    db_session,
) -> None:
    post = _seed_post(db_session, upvotes=0, downvotes=0)
    _seed_hidden_policy(db_session, run_id="run-one-vote")
    state = db_session.query(RunPolicyState).filter(RunPolicyState.run_id == "run-one-vote").first()
    assert state is not None
    state.policy_json = {
        "env": {
            "moltbook": {
                "feed": {
                    "vote_visibility": "hidden",
                    "hide_comment_counts": True,
                    "one_vote_per_post": True,
                    "hide_voted_posts_for_agent": True,
                }
            }
        }
    }
    db_session.add(state)
    db_session.commit()

    first = authenticated_api_client.post(
        f"/votes/posts/{post.id}/upvote",
        headers={"x-run-id": "run-one-vote"},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["applied"] is True
    assert first_payload["already_voted"] is False
    assert first_payload["upvotes"] == 1
    assert first_payload["downvotes"] == 0

    second = authenticated_api_client.post(
        f"/votes/posts/{post.id}/upvote",
        headers={"x-run-id": "run-one-vote"},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["applied"] is False
    assert second_payload["already_voted"] is True
    assert second_payload["upvotes"] == 1
    assert second_payload["downvotes"] == 0

    persisted_vote = db_session.query(Vote).filter(Vote.post_id == post.id, Vote.agent_id == "viewer-1").one()
    assert persisted_vote.vote_type == VoteType.UPVOTE


def test_feed_filters_already_voted_posts_for_agent(
    authenticated_api_client,
    db_session,
) -> None:
    post = _seed_post(db_session, upvotes=0, downvotes=0)
    _seed_hidden_policy(db_session, run_id="run-filter")
    state = db_session.query(RunPolicyState).filter(RunPolicyState.run_id == "run-filter").first()
    assert state is not None
    state.policy_json = {
        "env": {
            "moltbook": {
                "feed": {
                    "vote_visibility": "hidden",
                    "hide_comment_counts": True,
                    "one_vote_per_post": True,
                    "hide_voted_posts_for_agent": True,
                }
            }
        }
    }
    db_session.add(state)
    db_session.commit()

    before = authenticated_api_client.get("/feed?sort=new&limit=10", headers={"x-run-id": "run-filter"})
    assert before.status_code == 200
    assert len(before.json()) == 1

    vote_response = authenticated_api_client.post(
        f"/votes/posts/{post.id}/downvote",
        headers={"x-run-id": "run-filter"},
    )
    assert vote_response.status_code == 200
    assert vote_response.json()["applied"] is True

    after = authenticated_api_client.get("/feed?sort=new&limit=10", headers={"x-run-id": "run-filter"})
    assert after.status_code == 200
    assert after.json() == []
