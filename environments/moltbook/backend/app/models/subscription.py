from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import uuid


def generate_uuid():
    return str(uuid.uuid4())


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(String, primary_key=True, default=generate_uuid)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    submolt_id = Column(String, ForeignKey("submolts.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    agent = relationship("Agent", back_populates="subscriptions")
    submolt = relationship("Submolt", back_populates="subscriptions")
    
    __table_args__ = (
        UniqueConstraint('agent_id', 'submolt_id', name='unique_subscription'),
    )


class SubmoltModerator(Base):
    __tablename__ = "submolt_moderators"

    id = Column(String, primary_key=True, default=generate_uuid)
    submolt_id = Column(String, ForeignKey("submolts.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    role = Column(String, default="moderator")  # "owner" or "moderator"
    added_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    submolt = relationship("Submolt", back_populates="moderators")
    
    __table_args__ = (
        UniqueConstraint('submolt_id', 'agent_id', name='unique_moderator'),
    )
