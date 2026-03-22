from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.schemas.agent import AgentMinimal


class PostBase(BaseModel):
    title: str
    content: Optional[str] = None
    url: Optional[str] = None


class PostCreate(BaseModel):
    submolt: str
    title: str
    content: Optional[str] = None
    url: Optional[str] = None


class SubmoltMinimal(BaseModel):
    name: str
    display_name: str

    class Config:
        from_attributes = True


class PostResponse(BaseModel):
    id: str
    title: str
    content: Optional[str] = None
    url: Optional[str] = None
    upvotes: int = 0
    downvotes: int = 0
    score: int = 0
    created_at: datetime
    author: AgentMinimal
    submolt: SubmoltMinimal
    comment_count: int = 0
    is_pinned: bool = False

    class Config:
        from_attributes = True


class PostList(BaseModel):
    posts: list[PostResponse]
    total: int
