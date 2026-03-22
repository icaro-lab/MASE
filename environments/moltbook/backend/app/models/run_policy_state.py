from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.agent import generate_uuid


class RunPolicyState(Base):
    """Run-scoped policy handoff state for platform bootstrap."""

    __tablename__ = "run_policy_states"

    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, unique=True, index=True, nullable=False)
    environment_id = Column(String, nullable=True, index=True)
    policy_hash = Column(String, nullable=True, index=True)
    manifest_hash = Column(String, nullable=True, index=True)
    assignment_hash = Column(String, nullable=True, index=True)
    policy_json = Column(JSON, nullable=True)
    assignment_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
