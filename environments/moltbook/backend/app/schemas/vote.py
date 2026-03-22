from pydantic import BaseModel
from typing import Optional


class VoteResponse(BaseModel):
    success: bool
    message: str
    applied: bool = True
    already_voted: bool = False
    new_score: Optional[int] = None
    upvotes: Optional[int] = None
    downvotes: Optional[int] = None
    author: Optional[dict] = None
    already_following: Optional[bool] = None
    suggestion: Optional[str] = None
