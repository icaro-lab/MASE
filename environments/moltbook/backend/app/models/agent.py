from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import uuid


def generate_uuid():
    return str(uuid.uuid4())


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    api_key = Column(String, unique=True, index=True, nullable=False)
    verification_code = Column(String, nullable=False)
    
    # Status
    is_claimed = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    
    # Karma and reputation
    karma = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_active = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Optional metadata (using different name to avoid SQLAlchemy reserved word)
    agent_metadata = Column(JSON, nullable=True)
    avatar_url = Column(String, nullable=True)
    
    # Relationships
    posts = relationship("Post", back_populates="author", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="author", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="agent", cascade="all, delete-orphan")
    comment_votes = relationship("CommentVote", back_populates="agent", cascade="all, delete-orphan")
    
    # Following relationships
    following = relationship(
        "Follow",
        foreign_keys="Follow.follower_id",
        back_populates="follower",
        cascade="all, delete-orphan"
    )
    followers = relationship(
        "Follow", 
        foreign_keys="Follow.following_id",
        back_populates="following",
        cascade="all, delete-orphan"
    )
    
    # Submolt subscriptions
    subscriptions = relationship("Subscription", back_populates="agent", cascade="all, delete-orphan")
    
    # Owned submolts
    owned_submolts = relationship("Submolt", back_populates="owner")

    @property
    def follower_count(self):
        return len(self.followers)
    
    @property
    def following_count(self):
        return len(self.following)
