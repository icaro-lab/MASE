from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.agent import generate_uuid


class CompassSubmission(Base):
    """Run/agent-scoped compass submission for gating and analysis."""

    __tablename__ = "compass_submissions"

    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, index=True, nullable=False)
    agent_id = Column(String, index=True, nullable=False)
    instrument_version = Column(String, nullable=False, default="dummy_v0")
    accepted = Column(Boolean, nullable=False, default=False)
    refusal_reason = Column(String, nullable=True)
    answers_json = Column(JSON, nullable=True)
    score_json = Column(JSON, nullable=True)
    score_total = Column(Integer, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
