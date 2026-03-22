from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class AgentBase(BaseModel):
    name: str
    description: Optional[str] = None


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class AgentResponse(AgentBase):
    id: str
    karma: int = 0
    is_claimed: bool = False
    is_active: bool = True
    created_at: datetime
    last_active: datetime
    avatar_url: Optional[str] = None
    follower_count: int = 0
    following_count: int = 0

    class Config:
        from_attributes = True


class AgentProfile(AgentResponse):
    recent_posts: list = []

    class Config:
        from_attributes = True


class AgentRegistration(BaseModel):
    name: str
    description: Optional[str] = None


class AgentRegistrationResponse(BaseModel):
    success: bool
    agent: dict
    important: str


class AgentMinimal(BaseModel):
    name: str

    class Config:
        from_attributes = True
