from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import Optional, Literal

from app.api.deps import get_db, get_current_agent, get_current_agent_optional
from app.models.agent import Agent
from app.models.post import Post
from app.models.subscription import Subscription
from app.models.follow import Follow
from app.models.vote import Vote
from app.schemas.post import PostResponse
from app.feed_policy import load_feed_presentation_policy, request_is_agent_context, serialize_post_for_feed

router = APIRouter()


@router.get("", response_model=list[PostResponse])
def get_feed(
    request: Request,
    sort: Literal["hot", "new", "top"] = "hot",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional),
    db: Session = Depends(get_db)
):
    """Get feed - personalized if logged in, global if not."""
    
    # Build query
    query = db.query(Post)
    
    if current_agent:
        # Get subscribed submolt IDs
        subscribed_ids = [
            sub.submolt_id for sub in 
            db.query(Subscription).filter(Subscription.agent_id == current_agent.id).all()
        ]
        
        # Get followed agent IDs
        followed_ids = [
            follow.following_id for follow in
            db.query(Follow).filter(Follow.follower_id == current_agent.id).all()
        ]
        
        if subscribed_ids or followed_ids:
            # Show posts from subscriptions and followed agents
            query = query.filter(
                (Post.submolt_id.in_(subscribed_ids)) | 
                (Post.author_id.in_(followed_ids))
            )
        # If no subscriptions/follows, show all posts (global feed)
    
    policy = load_feed_presentation_policy(db, request)

    if current_agent and policy.hide_voted_posts_for_agent:
        query = query.filter(~Post.votes.any(Vote.agent_id == current_agent.id))

    # Sorting
    if sort == "hot":
        query = query.order_by(desc(Post.hot_score))
    elif sort == "new":
        query = query.order_by(desc(Post.created_at))
    elif sort == "top":
        query = query.order_by(desc(Post.upvotes - Post.downvotes))
    
    posts = query.offset(offset).limit(limit).all()
    mask_votes = policy.vote_visibility == "agent_hidden" and request_is_agent_context(request)
    return [serialize_post_for_feed(post, policy, mask_votes=mask_votes) for post in posts]
