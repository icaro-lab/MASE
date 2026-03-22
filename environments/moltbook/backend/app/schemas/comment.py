from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.schemas.agent import AgentMinimal


class CommentBase(BaseModel):
    content: str


class CommentCreate(BaseModel):
    content: str
    parent_id: Optional[str] = None


class CommentResponse(BaseModel):
    id: str
    content: str
    upvotes: int = 0
    downvotes: int = 0
    score: int = 0
    created_at: datetime
    author: AgentMinimal
    parent_id: Optional[str] = None
    replies: list = []

    class Config:
        from_attributes = True
