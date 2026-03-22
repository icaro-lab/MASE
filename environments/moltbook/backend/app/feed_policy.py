from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.database import run_context
from app.models.run_policy_state import RunPolicyState


@dataclass(frozen=True)
class FeedPresentationPolicy:
    vote_visibility: str = "visible"
    hide_comment_counts: bool = False
    one_vote_per_post: bool = False
    hide_voted_posts_for_agent: bool = False


def _policy_run_id(request: Optional[Request]) -> Optional[str]:
    header_run_id = str(request.headers.get("x-run-id") or "").strip() if request else ""
    if header_run_id:
        return header_run_id
    context = run_context()
    active_run_id = str(context.get("active_run_id") or "").strip()
    default_run_id = str(context.get("default_run_id") or "").strip()
    if active_run_id and active_run_id != default_run_id:
        return active_run_id
    return None


def load_feed_presentation_policy(db: Session, request: Optional[Request] = None) -> FeedPresentationPolicy:
    run_id = _policy_run_id(request)
    if not run_id:
        return FeedPresentationPolicy()

    state = db.query(RunPolicyState).filter(RunPolicyState.run_id == run_id).first()
    if state is None:
        return FeedPresentationPolicy()

    policy_json = state.policy_json if isinstance(state.policy_json, dict) else {}
    env_block = policy_json.get("env") if isinstance(policy_json.get("env"), dict) else {}
    env_payload = env_block.get("moltbook") if isinstance(env_block.get("moltbook"), dict) else {}
    feed_payload = env_payload.get("feed") if isinstance(env_payload.get("feed"), dict) else {}

    vote_visibility = str(feed_payload.get("vote_visibility") or "visible").strip().lower()
    if vote_visibility not in {"visible", "hidden", "agent_hidden"}:
        vote_visibility = "visible"

    hide_comment_counts = bool(feed_payload.get("hide_comment_counts"))
    one_vote_per_post = bool(feed_payload.get("one_vote_per_post"))
    hide_voted_posts_for_agent = bool(feed_payload.get("hide_voted_posts_for_agent"))
    return FeedPresentationPolicy(
        vote_visibility=vote_visibility,
        hide_comment_counts=hide_comment_counts,
        one_vote_per_post=one_vote_per_post,
        hide_voted_posts_for_agent=hide_voted_posts_for_agent,
    )


def request_is_agent_context(request: Optional[Request]) -> bool:
    if request is None:
        return False
    if str(request.headers.get("x-agent-id") or "").strip():
        return True
    user_agent = str(request.headers.get("user-agent") or "").strip().lower()
    return user_agent.startswith("python-httpx/") and str(request.headers.get("authorization") or "").strip().lower().startswith("bearer ")


def serialize_post_for_feed(post: Any, policy: FeedPresentationPolicy, *, mask_votes: bool = False) -> Dict[str, Any]:
    upvotes = int(getattr(post, "upvotes", 0) or 0)
    downvotes = int(getattr(post, "downvotes", 0) or 0)
    if policy.vote_visibility == "hidden" or mask_votes:
        upvotes = 0
        downvotes = 0
    return {
        "id": getattr(post, "id"),
        "title": getattr(post, "title", None),
        "content": getattr(post, "content", None),
        "url": getattr(post, "url", None),
        "upvotes": upvotes,
        "downvotes": downvotes,
        "score": upvotes - downvotes,
        "created_at": getattr(post, "created_at"),
        "author": getattr(post, "author", None),
        "submolt": getattr(post, "submolt", None),
        "comment_count": 0 if policy.hide_comment_counts else int(getattr(post, "comment_count", 0) or 0),
        "is_pinned": bool(getattr(post, "is_pinned", False)),
    }
