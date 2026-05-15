from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from .database import Base

class RepoEvent(Base):
    __tablename__ = "repo_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String) # commit, pr, issue
    external_id = Column(String, unique=True) # commit sha or pr/issue number
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class AgentAdvice(Base):
    __tablename__ = "agent_advice"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer) # Linked to repo_events id if applicable
    advice_type = Column(String) # security, update, info
    title = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
