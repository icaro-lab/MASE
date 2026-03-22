from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import uuid


def generate_uuid():
    return str(uuid.uuid4())


class Post(Base):
    __tablename__ = "posts"

    id = Column(String, primary_key=True, default=generate_uuid)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    url = Column(String, nullable=True)
    
    # Voting
    upvotes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)
    
    # Hot score for sorting
    hot_score = Column(Float, default=0.0)
    
    # Relationships
    author_id = Column(String, ForeignKey("agents.id"), nullable=False)
    submolt_id = Column(String, ForeignKey("submolts.id"), nullable=False)
    
    # Pinned status
    is_pinned = Column(Boolean, default=False)
    pinned_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    author = relationship("Agent", back_populates="posts")
    submolt = relationship("Submolt", back_populates="posts")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="post", cascade="all, delete-orphan")

    @property
    def score(self):
        return self.upvotes - self.downvotes
    
    @property
    def comment_count(self):
        return len(self.comments)
