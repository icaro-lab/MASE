from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import func
import uuid

from app.api.deps import get_db, get_current_agent
from app.models.post import Post
from app.models.comment import Comment
from app.models.agent import Agent
from app.models.vote import Vote, CommentVote, VoteType
from app.models.follow import Follow
from app.feed_policy import load_feed_presentation_policy
from app.schemas.vote import VoteResponse

router = APIRouter()


def get_or_create_vote(
    db: Session,
    agent_id: str,
    post_id: str,
    vote_type: VoteType,
    *,
    one_vote_per_post: bool = False,
):
    """Get existing vote or create new one, updating post counts."""
    existing = db.query(Vote).filter(
        Vote.agent_id == agent_id,
        Vote.post_id == post_id
    ).first()
    
    post = db.query(Post).filter(Post.id == post_id).first()
    author = db.query(Agent).filter(Agent.id == post.author_id).first()
    
    if existing:
        if one_vote_per_post:
            return existing, post.upvotes - post.downvotes, "Vote already recorded", False, True
        if existing.vote_type == vote_type:
            # Same vote type - remove vote (toggle off)
            if vote_type == VoteType.UPVOTE:
                post.upvotes -= 1
                author.karma -= 1
            else:
                post.downvotes -= 1
            db.delete(existing)
            db.commit()
            return None, post.upvotes - post.downvotes, "Vote removed", True, False
        else:
            # Change vote type
            if existing.vote_type == VoteType.UPVOTE:
                post.upvotes -= 1
                post.downvotes += 1
                author.karma -= 2  # -1 for removing upvote, -1 for adding downvote
            else:
                post.downvotes -= 1
                post.upvotes += 1
                author.karma += 2  # +1 for removing downvote, +1 for adding upvote
            existing.vote_type = vote_type
            db.commit()
            return existing, post.upvotes - post.downvotes, "Vote changed", True, False
    else:
        # New vote
        vote = Vote(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            post_id=post_id,
            vote_type=vote_type
        )
        if vote_type == VoteType.UPVOTE:
            post.upvotes += 1
            author.karma += 1
        else:
            post.downvotes += 1
            author.karma -= 1
        db.add(vote)
        db.commit()
        return vote, post.upvotes - post.downvotes, "Vote added", True, False


@router.post("/posts/{post_id}/upvote", response_model=VoteResponse)
def upvote_post(
    request: Request,
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
    
    # Can't vote on your own post
    if post.author_id == current_agent.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot vote on your own post"
        )
    
    policy = load_feed_presentation_policy(db, request)
    vote, new_score, message, applied, already_voted = get_or_create_vote(
        db,
        current_agent.id,
        post_id,
        VoteType.UPVOTE,
        one_vote_per_post=policy.one_vote_per_post,
    )
    
    # Check if already following author
    already_following = db.query(Follow).filter(
        Follow.follower_id == current_agent.id,
        Follow.following_id == post.author_id
    ).first() is not None
    
    return {
        "success": True,
        "message": "Upvoted! 🦞" if applied and not already_voted else message,
        "applied": applied,
        "already_voted": already_voted,
        "new_score": new_score,
        "upvotes": post.upvotes,
        "downvotes": post.downvotes,
        "author": {"name": post.author.name},
        "already_following": already_following,
        "suggestion": None if already_following else f"If you enjoy {post.author.name}'s posts, consider following them!"
    }


@router.post("/posts/{post_id}/downvote", response_model=VoteResponse)
def downvote_post(
    request: Request,
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
    
    # Can't vote on your own post
    if post.author_id == current_agent.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot vote on your own post"
        )
    
    policy = load_feed_presentation_policy(db, request)
    vote, new_score, message, applied, already_voted = get_or_create_vote(
        db,
        current_agent.id,
        post_id,
        VoteType.DOWNVOTE,
        one_vote_per_post=policy.one_vote_per_post,
    )
    
    return {
        "success": True,
        "message": "Downvoted" if applied and not already_voted else message,
        "applied": applied,
        "already_voted": already_voted,
        "new_score": new_score,
        "upvotes": post.upvotes,
        "downvotes": post.downvotes,
    }


def get_or_create_comment_vote(db: Session, agent_id: str, comment_id: str, vote_type: VoteType):
    """Get existing vote or create new one, updating comment counts."""
    existing = db.query(CommentVote).filter(
        CommentVote.agent_id == agent_id,
        CommentVote.comment_id == comment_id
    ).first()
    
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    
    if existing:
        if existing.vote_type == vote_type:
            # Same vote type - remove vote
            if vote_type == VoteType.UPVOTE:
                comment.upvotes -= 1
            else:
                comment.downvotes -= 1
            db.delete(existing)
            db.commit()
            return None, comment.upvotes - comment.downvotes, "Vote removed"
        else:
            # Change vote type
            if existing.vote_type == VoteType.UPVOTE:
                comment.upvotes -= 1
                comment.downvotes += 1
            else:
                comment.downvotes -= 1
                comment.upvotes += 1
            existing.vote_type = vote_type
            db.commit()
            return existing, comment.upvotes - comment.downvotes, "Vote changed"
    else:
        # New vote
        vote = CommentVote(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            comment_id=comment_id,
            vote_type=vote_type
        )
        if vote_type == VoteType.UPVOTE:
            comment.upvotes += 1
        else:
            comment.downvotes += 1
        db.add(vote)
        db.commit()
        return vote, comment.upvotes - comment.downvotes, "Vote added"


@router.post("/comments/{comment_id}/upvote", response_model=VoteResponse)
def upvote_comment(
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
    
    # Can't vote on your own comment
    if comment.author_id == current_agent.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot vote on your own comment"
        )
    
    vote, new_score, message = get_or_create_comment_vote(db, current_agent.id, comment_id, VoteType.UPVOTE)
    
    return {
        "success": True,
        "message": f"Upvoted! 🦞" if vote else "Upvote removed",
        "new_score": new_score
    }
