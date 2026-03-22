from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.agent import generate_uuid


class RunContextState(Base):
    """Run-scoped environment context captured during /run/init."""

    __tablename__ = "run_context_states"

    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, unique=True, index=True, nullable=False)
    environment_id = Column(String, nullable=True, index=True)
    params_json = Column(JSON, nullable=True)
    assignment_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
