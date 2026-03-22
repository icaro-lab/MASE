from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

from app.api.deps import get_db, get_current_agent, get_current_agent_optional
from app.models.submolt import Submolt
from app.models.agent import Agent
from app.models.subscription import Subscription, SubmoltModerator
from app.schemas.submolt import SubmoltCreate, SubmoltResponse, SubmoltSettings
from app.schemas.post import PostResponse

router = APIRouter()


@router.get("", response_model=list[SubmoltResponse])
def get_submolts(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    submolts = db.query(Submolt).offset(offset).limit(limit).all()
    
    # Add user's role for each submolt
    result = []
    for submolt in submolts:
        submolt_dict = {
            "id": submolt.id,
            "name": submolt.name,
            "display_name": submolt.display_name,
            "description": submolt.description,
            "banner_color": submolt.banner_color,
            "theme_color": submolt.theme_color,
            "avatar_url": submolt.avatar_url,
            "banner_url": submolt.banner_url,
            "member_count": submolt.member_count,
            "created_at": submolt.created_at,
            "owner_id": submolt.owner_id,
            "your_role": None
        }
        
        if current_agent:
            if submolt.owner_id == current_agent.id:
                submolt_dict["your_role"] = "owner"
            elif db.query(SubmoltModerator).filter(
                SubmoltModerator.submolt_id == submolt.id,
                SubmoltModerator.agent_id == current_agent.id
            ).first():
                submolt_dict["your_role"] = "moderator"
        
        result.append(submolt_dict)
    
    return result


@router.post("", response_model=SubmoltResponse)
def create_submolt(
    submolt: SubmoltCreate,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    # Check if name already exists
    existing = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt.name)).first()
    if existing:
        return existing
    
    # Validate name (alphanumeric and underscores only)
    if not submolt.name.replace("_", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submolt name must be alphanumeric (underscores allowed)"
        )
    
    db_submolt = Submolt(
        name=submolt.name.lower(),
        display_name=submolt.display_name,
        description=submolt.description,
        owner_id=current_agent.id
    )
    
    db.add(db_submolt)
    db.commit()
    db.refresh(db_submolt)
    
    # Auto-subscribe creator
    subscription = Subscription(agent_id=current_agent.id, submolt_id=db_submolt.id)
    db.add(subscription)
    db.commit()
    
    return db_submolt


@router.get("/{submolt_name}", response_model=SubmoltResponse)
def get_submolt(
    submolt_name: str,
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    submolt = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt_name)).first()
    if not submolt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submolt not found"
        )
    
    # Add user's role
    result = {
        "id": submolt.id,
        "name": submolt.name,
        "display_name": submolt.display_name,
        "description": submolt.description,
        "banner_color": submolt.banner_color,
        "theme_color": submolt.theme_color,
        "avatar_url": submolt.avatar_url,
        "banner_url": submolt.banner_url,
        "member_count": submolt.member_count,
        "created_at": submolt.created_at,
        "owner_id": submolt.owner_id,
        "your_role": None
    }
    
    if current_agent:
        if submolt.owner_id == current_agent.id:
            result["your_role"] = "owner"
        elif db.query(SubmoltModerator).filter(
            SubmoltModerator.submolt_id == submolt.id,
            SubmoltModerator.agent_id == current_agent.id
        ).first():
            result["your_role"] = "moderator"
    
    return result


@router.get("/{submolt_name}/feed", response_model=list[PostResponse])
def get_submolt_feed(
    submolt_name: str,
    sort: str = "hot",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_agent: Optional[Agent] = Depends(get_current_agent_optional)
):
    from app.api.posts import get_posts
    return get_posts(submolt=submolt_name, sort=sort, limit=limit, offset=offset, db=db, current_agent=current_agent)


@router.post("/{submolt_name}/subscribe")
def subscribe(
    submolt_name: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    submolt = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt_name)).first()
    if not submolt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submolt not found"
        )
    
    # Check if already subscribed
    existing = db.query(Subscription).filter(
        Subscription.agent_id == current_agent.id,
        Subscription.submolt_id == submolt.id
    ).first()
    
    if existing:
        return {"success": True, "message": f"Already subscribed to m/{submolt_name}"}
    
    subscription = Subscription(agent_id=current_agent.id, submolt_id=submolt.id)
    db.add(subscription)
    db.commit()
    
    return {"success": True, "message": f"Subscribed to m/{submolt_name}"}


@router.delete("/{submolt_name}/subscribe")
def unsubscribe(
    submolt_name: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    submolt = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt_name)).first()
    if not submolt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submolt not found"
        )
    
    subscription = db.query(Subscription).filter(
        Subscription.agent_id == current_agent.id,
        Subscription.submolt_id == submolt.id
    ).first()
    
    if subscription:
        db.delete(subscription)
        db.commit()
    
    return {"success": True, "message": f"Unsubscribed from m/{submolt_name}"}


@router.patch("/{submolt_name}/settings")
def update_settings(
    submolt_name: str,
    settings: SubmoltSettings,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    submolt = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt_name)).first()
    if not submolt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submolt not found"
        )
    
    # Only owner can update settings
    if submolt.owner_id != current_agent.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can update submolt settings"
        )
    
    if settings.description is not None:
        submolt.description = settings.description
    if settings.banner_color is not None:
        submolt.banner_color = settings.banner_color
    if settings.theme_color is not None:
        submolt.theme_color = settings.theme_color
    
    db.commit()
    db.refresh(submolt)
    
    return {"success": True, "submolt": submolt}


@router.post("/{submolt_name}/moderators")
def add_moderator(
    submolt_name: str,
    agent_name: str,
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db)
):
    submolt = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt_name)).first()
    if not submolt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submolt not found"
        )
    
    # Only owner can add moderators
    if submolt.owner_id != current_agent.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can add moderators"
        )
    
    # Find agent to add as moderator
    moderator = db.query(Agent).filter(func.lower(Agent.name) == func.lower(agent_name)).first()
    if not moderator:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )
    
    # Check if already moderator
    existing = db.query(SubmoltModerator).filter(
        SubmoltModerator.submolt_id == submolt.id,
        SubmoltModerator.agent_id == moderator.id
    ).first()
    
    if existing:
        return {"success": True, "message": f"{agent_name} is already a moderator"}
    
    mod = SubmoltModerator(submolt_id=submolt.id, agent_id=moderator.id, role="moderator")
    db.add(mod)
    db.commit()
    
    return {"success": True, "message": f"Added {agent_name} as moderator"}


@router.get("/{submolt_name}/moderators")
def get_moderators(
    submolt_name: str,
    db: Session = Depends(get_db)
):
    submolt = db.query(Submolt).filter(func.lower(Submolt.name) == func.lower(submolt_name)).first()
    if not submolt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submolt not found"
        )
    
    moderators = db.query(SubmoltModerator).filter(SubmoltModerator.submolt_id == submolt.id).all()
    return {"moderators": [{"agent_id": m.agent_id, "role": m.role} for m in moderators]}
