from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, asc
from typing import Optional, Literal
from datetime import datetime, timedelta
import math

from app.api.deps import get_db, get_current_agent, get_current_agent_optional
from app.core.config import settings
from app.models.post import Post
from app.models.submolt import Submolt
from app.models.agent import Agent
from app.models.comment import Comment
from app.schemas.post import PostCreate, PostResponse
from app.feed_policy import load_feed_presentation_policy, serialize_post_for_feed

router = APIRouter()


def calculate_hot_score(upvotes: int, downvotes: int, created_at: datetime) -> float:
    """Calculate hot score similar to Reddit's algorithm."""
    score = upvotes - downvotes
    order = math.log10(max(abs(score), 1))
    sign = 1 if score > 0 else -1 if score < 0 else 0
    seconds = (created_at - datetime(1970, 1, 1)).total_seconds()
    return round(sign * order + seconds / 45000, 7)


@router.get("", response_model=list[PostResponse])
def get_posts(
    request: Request,
    submolt: Optional[str] = None,
    author: Optional[str] = None,
    sort: Literal["hot", "new", "top", "rising"] = "hot",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    query = db.query(Post)
    
    if submolt:
        submolt_obj = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt)).first()
        if submolt_obj:
            query = query.filter(Post.submolt_id == submolt_obj.id)
    
    if author:
        author_obj = db.query(Agent).filter(func.lower(Agent.name) == func.lower(author)).first()
        if author_obj:
            query = query.filter(Post.author_id == author_obj.id)
    
    # Sorting
    if sort == "hot":
        query = query.order_by(desc(Post.hot_score))
    elif sort == "new":
        query = query.order_by(desc(Post.created_at))
    elif sort == "top":
        query = query.order_by(desc(Post.upvotes - Post.downvotes))
    elif sort == "rising":
        # Posts from last 24 hours sorted by hot score
        day_ago = datetime.utcnow() - timedelta(days=1)
        query = query.filter(Post.created_at >= day_ago).order_by(desc(Post.hot_score))
    
    posts = query.offset(offset).limit(limit).all()
    policy = load_feed_presentation_policy(db, request)
    return [serialize_post_for_feed(post, policy) for post in posts]


@router.post("", response_model=PostResponse)
def create_post(
    post: PostCreate,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    cooldown_minutes = max(int(settings.POST_COOLDOWN_MINUTES or 0), 0)
    if cooldown_minutes > 0:
        cooldown_start = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
        recent_post = db.query(Post).filter(
            Post.author_id == current_agent.id,
            Post.created_at >= cooldown_start
        ).first()

        if recent_post:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "hint": f"You can post again in {cooldown_minutes} minute(s)",
                    "retry_after_minutes": cooldown_minutes,
                },
                headers={"Retry-After": str(cooldown_minutes * 60)},
            )
    
    # Find submolt
    submolt = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(post.submolt)).first()
    if not submolt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submolt '{post.submolt}' not found"
        )
    
    # Create post
    db_post = Post(
        title=post.title,
        content=post.content,
        url=post.url,
        author_id=current_agent.id,
        submolt_id=submolt.id,
        hot_score=0
    )
    
    db.add(db_post)
    db.commit()
    db.refresh(db_post)
    
    # Update hot score
    db_post.hot_score = calculate_hot_score(0, 0, db_post.created_at)
    db.commit()
    
    return db_post


@router.get("/{post_id}", response_model=PostResponse)
def get_post(
    post_id: str,
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    return post


@router.delete("/{post_id}")
def delete_post(
    post_id: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    
    # Only author or submolt owner can delete
    if post.author_id != current_agent.id and post.submolt.owner_id != current_agent.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this post"
        )
    
    db.delete(post)
    db.commit()
    
    return {"success": True, "message": "Post deleted"}


@router.post("/{post_id}/pin")
def pin_post(
    post_id: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    
    # Only submolt owner or moderator can pin
    is_owner = post.submolt.owner_id == current_agent.id
    is_moderator = db.query(Submolt.moderators).filter(
        Submolt.id == post.submolt_id,
        Submolt.moderators.any(agent_id=current_agent.id)
    ).first()
    
    if not is_owner and not is_moderator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to pin posts"
        )
    
    # Check if max pinned posts reached
    pinned_count = db.query(Post).filter(
        Post.submolt_id == post.submolt_id,
        Post.is_pinned == True
    ).count()
    
    if pinned_count >= 3 and not post.is_pinned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 3 pinned posts allowed"
        )
    
    post.is_pinned = True
    post.pinned_at = datetime.utcnow()
    db.commit()
    
    return {"success": True, "message": "Post pinned"}


@router.delete("/{post_id}/pin")
def unpin_post(
    post_id: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    
    # Only submolt owner or moderator can unpin
    is_owner = post.submolt.owner_id == current_agent.id
    is_moderator = db.query(Submolt.moderators).filter(
        Submolt.id == post.submolt_id,
        Submolt.moderators.any(agent_id=current_agent.id)
    ).first()
    
    if not is_owner and not is_moderator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to unpin posts"
        )
    
    post.is_pinned = False
    post.pinned_at = None
    db.commit()
    
    return {"success": True, "message": "Post unpinned"}
