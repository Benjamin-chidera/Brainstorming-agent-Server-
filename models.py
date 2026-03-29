from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime, timezone

class MeetingAgentLink(SQLModel, table=True):
    meeting_id: Optional[int] = Field(default=None, foreign_key="meeting.id", primary_key=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agents.id", primary_key=True)

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    full_name: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Provider info for social auth
    google_id: Optional[str] = Field(default=None, index=True)
    github_id: Optional[str] = Field(default=None, index=True)
    
    agents: List["Agents"] = Relationship(back_populates="user")
    meetings: List["Meeting"] = Relationship(back_populates="user")

class OTP(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    code: str
    expires_at: datetime
    is_used: bool = Field(default=False)
    full_name: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentsBase(SQLModel):
    gender: str
    bio: str
    accent: str
    avatarUrl: str
    tone: str   
    voice: str

class AgentsCreate(AgentsBase):
    pass

class Agents(AgentsBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    user: Optional[User] = Relationship(back_populates="agents")
    meetings: List["Meeting"] = Relationship(back_populates="agents", link_model=MeetingAgentLink)


class Meeting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # about: str = Field(index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")

    status: str = Field(default="active") ## active, paused, ended  

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))  
    
    user: Optional[User] = Relationship(back_populates="meetings")
    agents: List["Agents"] = Relationship(back_populates="meetings", link_model=MeetingAgentLink)

class MeetingCreate(SQLModel):
    agentIds: List[int]
    status: str = "active"