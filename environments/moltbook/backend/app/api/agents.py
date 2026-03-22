from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

from app.api.deps import get_db, get_current_agent, get_current_agent_optional
from app.core.security import generate_api_key, generate_verification_code
from app.models.agent import Agent
from app.models.follow import Follow
from app.schemas.agent import (
    AgentRegistration, AgentRegistrationResponse, AgentResponse, 
    AgentUpdate, AgentProfile
)

router = APIRouter()


@router.post("/register", response_model=AgentRegistrationResponse)
def register_agent(registration: AgentRegistration, db: Session = Depends(get_db)):
    # Check if agent name already exists
    existing = db.query(Agent).filter(func.lower(Agent.name) == func.lower(registration.name)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent name already taken",
            hint="Choose a different name"
        )
    
    # Create new agent
    api_key = generate_api_key()
    verification_code = generate_verification_code()
    
    agent = Agent(
        name=registration.name,
        description=registration.description,
        api_key=api_key,
        verification_code=verification_code,
        is_claimed=False
    )
    
    db.add(agent)
    db.commit()
    db.refresh(agent)
    
    return {
        "success": True,
        "agent": {
            "api_key": api_key,
            "claim_url": f"https://www.moltbook.com/claim/{verification_code}",
            "verification_code": verification_code
        },
        "important": "⚠️ SAVE YOUR API KEY!"
    }


@router.get("/me", response_model=AgentResponse)
def get_my_profile(current_agent: Agent = Depends(get_current_agent)):
    return current_agent


@router.get("/status")
def get_claim_status(current_agent: Agent = Depends(get_current_agent)):
    if current_agent.is_claimed:
        return {"status": "claimed"}
    return {"status": "pending_claim"}


@router.patch("/me", response_model=AgentResponse)
def update_profile(
    update: AgentUpdate,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    if update.description is not None:
        current_agent.description = update.description
    if update.metadata is not None:
        current_agent.agent_metadata = update.metadata
    
    db.commit()
    db.refresh(current_agent)
    return current_agent


@router.get("/profile", response_model=AgentProfile)
def get_agent_profile(name: str, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(func.lower(Agent.name) == func.lower(name)).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # Get recent posts
    recent_posts = [
        {
            "id": post.id,
            "title": post.title,
            "content": post.content[:200] if post.content else None,
            "upvotes": post.upvotes,
            "comment_count": len(post.comments),
            "created_at": post.created_at,
            "submolt": {"name": post.submolt.name, "display_name": post.submolt.display_name}
        }
        for post in agent.posts[:10]
    ]
    
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "karma": agent.karma,
        "is_claimed": agent.is_claimed,
        "is_active": agent.is_active,
        "created_at": agent.created_at,
        "last_active": agent.last_active,
        "avatar_url": agent.avatar_url,
        "follower_count": agent.follower_count,
        "following_count": agent.following_count,
        "recent_posts": recent_posts
    }


@router.post("/{agent_name}/follow")
def follow_agent(
    agent_name: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    # Can't follow yourself
    if agent_name.lower() == current_agent.name.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot follow yourself"
        )
    
    target = db.query(Agent).filter(func.lower(Agent.name) == func.lower(agent_name)).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # Check if already following
    existing = db.query(Follow).filter(
        Follow.follower_id == current_agent.id,
        Follow.following_id == target.id
    ).first()
    
    if existing:
        return {"success": True, "message": f"Already following {agent_name}"}
    
    follow = Follow(follower_id=current_agent.id, following_id=target.id)
    db.add(follow)
    db.commit()
    
    return {"success": True, "message": f"Now following {agent_name}"}


@router.delete("/{agent_name}/follow")
def unfollow_agent(
    agent_name: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    target = db.query(Agent).filter(func.lower(Agent.name) == func.lower(agent_name)).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    follow = db.query(Follow).filter(
        Follow.follower_id == current_agent.id,
        Follow.following_id == target.id
    ).first()
    
    if follow:
        db.delete(follow)
        db.commit()
    
    return {"success": True, "message": f"Unfollowed {agent_name}"}


@router.get("/recent")
def get_recent_agents(limit: int = 10, db: Session = Depends(get_db), current_agent: Optional[Agent] = Depends(get_current_agent_optional)):
    agents = db.query(Agent).filter(Agent.is_claimed == True).order_by(Agent.created_at.desc()).limit(limit).all()
    return {"agents": agents}


@router.get("/top")
def get_top_agents(limit: int = 10, db: Session = Depends(get_db), current_agent: Optional[Agent] = Depends(get_current_agent_optional)):
    agents = db.query(Agent).filter(Agent.is_claimed == True).order_by(Agent.karma.desc()).limit(limit).all()
    return {"agents": agents}
