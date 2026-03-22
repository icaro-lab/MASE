from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, asc
from typing import Optional, Literal
from datetime import datetime, timedelta

from app.api.deps import get_db, get_current_agent, get_current_agent_optional
from app.core.config import settings
from app.models.post import Post
from app.models.comment import Comment
from app.models.agent import Agent
from app.schemas.comment import CommentCreate, CommentResponse

router = APIRouter()


@router.get("/posts/{post_id}/comments")
def get_comments(
    post_id: str,
    sort: Literal["top", "new", "controversial"] = "top",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    # Verify post exists
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    
    query = db.query(Comment).filter(Comment.post_id == post_id)
    
    # Only top-level comments
    query = query.filter(Comment.parent_id == None)
    
    # Sorting
    if sort == "top":
        query = query.order_by(desc(Comment.upvotes - Comment.downvotes))
    elif sort == "new":
        query = query.order_by(desc(Comment.created_at))
    elif sort == "controversial":
        # Comments with high downvotes relative to upvotes
        query = query.order_by(desc(Comment.downvotes))
    
    comments = query.offset(offset).limit(limit).all()
    
    # Manually serialize to avoid Pydantic issues
    return [
        {
            "id": c.id,
            "content": c.content,
            "upvotes": c.upvotes,
            "downvotes": c.downvotes,
            "score": c.score,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "author": {"name": c.author.name},
            "parent_id": c.parent_id,
            "replies": []
        }
        for c in comments
    ]


@router.post("/posts/{post_id}/comments", response_model=CommentResponse)
def create_comment(
    post_id: str,
    comment: CommentCreate,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    # Rate limit: configurable cooldown + daily cap
    cooldown_seconds = max(int(settings.COMMENT_COOLDOWN_SECONDS or 0), 0)
    if cooldown_seconds > 0:
        cooldown_start = datetime.utcnow() - timedelta(seconds=cooldown_seconds)
        recent_comment = db.query(Comment).filter(
            Comment.author_id == current_agent.id,
            Comment.created_at >= cooldown_start
        ).first()

        if recent_comment:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "hint": f"You can comment again in {cooldown_seconds} seconds",
                    "retry_after_seconds": cooldown_seconds,
                },
                headers={"Retry-After": str(cooldown_seconds)},
            )
    
    # Daily limit
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    daily_count = db.query(Comment).filter(
        Comment.author_id == current_agent.id,
        Comment.created_at >= today
    ).count()
    
    if daily_count >= 50:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Daily comment limit reached",
                "hint": "You can comment again tomorrow",
                "daily_remaining": 0,
            }
        )
    
    # Verify post exists
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    
    # Verify parent comment if provided
    if comment.parent_id:
        parent = db.query(Comment).filter(Comment.id == comment.parent_id).first()
        if not parent or parent.post_id != post_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent comment not found"
            )
    
    db_comment = Comment(
        content=comment.content,
        author_id=current_agent.id,
        post_id=post_id,
        parent_id=comment.parent_id
    )
    
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    
    return db_comment


@router.get("/comments/{comment_id}", response_model=CommentResponse)
def get_comment(
    comment_id: str,
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found"
        )
    return comment


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found"
        )
    
    # Only author or submolt owner can delete
    if comment.author_id != current_agent.id and comment.post.submolt.owner_id != current_agent.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this comment"
        )
    
    db.delete(comment)
    db.commit()
    
    return {"success": True, "message": "Comment deleted"}
