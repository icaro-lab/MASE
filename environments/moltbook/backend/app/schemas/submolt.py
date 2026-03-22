from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SubmoltBase(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None


class SubmoltCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None


class SubmoltSettings(BaseModel):
    description: Optional[str] = None
    banner_color: Optional[str] = None
    theme_color: Optional[str] = None


class SubmoltResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    banner_color: str = "#1a1a2e"
    theme_color: str = "#ff4500"
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    member_count: int = 0
    created_at: datetime
    owner_id: str
    your_role: Optional[str] = None  # "owner", "moderator", or None

    class Config:
        from_attributes = True


class SubmoltList(BaseModel):
    submolts: list[SubmoltResponse]
    total: int
