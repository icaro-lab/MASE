from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional, Literal

from app.api.deps import get_db, get_current_agent_optional
from app.models.post import Post
from app.models.comment import Comment
from app.models.agent import Agent
from app.models.submolt import Submolt

router = APIRouter()


@router.get("")
def search(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    type: Literal["posts", "comments", "all"] = "all",
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    """
    Search posts and comments by content.
    Note: This is a simple text search. For semantic search, the API would need
    vector embeddings which require additional infrastructure.
    """
    if not q.strip():
        raise HTTPException(
            status_code=400,
            detail="Search query is required"
        )
    
    results = []
    search_term = f"%{q}%"
    
    # Search posts
    if type in ["posts", "all"]:
        posts_query = db.query(Post).filter(
            or_(
                Post.title.ilike(search_term),
                Post.content.ilike(search_term)
            )
        ).order_by(Post.created_at.desc()).limit(limit)
        
        for post in posts_query.all():
            results.append({
                "id": post.id,
                "type": "post",
                "title": post.title,
                "content": post.content[:300] if post.content else None,
                "upvotes": post.upvotes,
                "downvotes": post.downvotes,
                "created_at": post.created_at.isoformat() if post.created_at else None,
                "author": {"name": post.author.name},
                "submolt": {"name": post.submolt.name, "display_name": post.submolt.display_name},
                "post_id": post.id,
                "comment_count": len(post.comments) if post.comments else 0
            })
    
    # Search comments
    if type in ["comments", "all"]:
        comments_limit = limit if type == "comments" else limit // 2
        comments_query = db.query(Comment).filter(
            Comment.content.ilike(search_term)
        ).order_by(Comment.created_at.desc()).limit(comments_limit)
        
        for comment in comments_query.all():
            results.append({
                "id": comment.id,
                "type": "comment",
                "title": None,
                "content": comment.content[:300],
                "upvotes": comment.upvotes,
                "downvotes": 0,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
                "author": {"name": comment.author.name},
                "post": {"id": comment.post.id, "title": comment.post.title},
                "post_id": comment.post.id
            })
    
    # Sort by relevance (simple: title match > content match)
    def relevance_score(item):
        score = 0
        title = item.get("title", "") or ""
        content = item.get("content", "") or ""
        query_lower = q.lower()
        
        # Exact title match gets highest score
        if query_lower in title.lower():
            score += 100
        # Title contains query words
        if any(word in title.lower() for word in query_lower.split()):
            score += 50
        # Content contains query
        if query_lower in content.lower():
            score += 10
        
        # Boost by upvotes
        score += item.get("upvotes", 0)
        
        return score
    
    results.sort(key=relevance_score, reverse=True)
    
    return {
        "success": True,
        "query": q,
        "type": type,
        "results": results[:limit],
        "count": len(results[:limit])
    }
