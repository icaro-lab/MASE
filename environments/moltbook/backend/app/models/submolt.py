from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import uuid


def generate_uuid():
    return str(uuid.uuid4())


class Submolt(Base):
    __tablename__ = "submolts"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    
    # Theme colors
    banner_color = Column(String, default="#1a1a2e")
    theme_color = Column(String, default="#ff4500")
    
    # Media
    avatar_url = Column(String, nullable=True)
    banner_url = Column(String, nullable=True)
    
    # Owner
    owner_id = Column(String, ForeignKey("agents.id"), nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    owner = relationship("Agent", back_populates="owned_submolts")
    posts = relationship("Post", back_populates="submolt")
    subscriptions = relationship("Subscription", back_populates="submolt", cascade="all, delete-orphan")
    moderators = relationship("SubmoltModerator", back_populates="submolt", cascade="all, delete-orphan")

    @property
    def member_count(self):
        return len(self.subscriptions)
