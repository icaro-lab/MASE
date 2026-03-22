from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
from app.core.database import get_db
from app.core.security import verify_token, generate_api_key, generate_verification_code
from app.models.agent import Agent


class OptionalHTTPBearer(HTTPBearer):
    """HTTPBearer that doesn't require authentication."""
    async def __call__(self, request: Request) -> Optional[HTTPAuthorizationCredentials]:
        try:
            return await super().__call__(request)
        except HTTPException:
            return None


security = HTTPBearer()
optional_security = OptionalHTTPBearer(auto_error=False)


def get_current_agent(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Agent:
    token = credentials.credentials
    
    # Try to find agent by API key
    agent = db.query(Agent).filter(Agent.api_key == token).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not agent.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent is deactivated",
        )
    
    return agent


def get_current_agent_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: Session = Depends(get_db)
) -> Optional[Agent]:
    if not credentials:
        return None
    try:
        token = credentials.credentials
        agent = db.query(Agent).filter(Agent.api_key == token).first()
        if not agent or not agent.is_active:
            return None
        return agent
    except Exception:
        return None
