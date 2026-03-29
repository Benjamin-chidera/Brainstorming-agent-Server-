from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from database import get_session
from models import User, Agents, Meeting, MeetingCreate
from sqlmodel import select
from utils.auth import get_current_user
from starlette import status

route = APIRouter(
    prefix="/agents",
    tags=["Start Meeting"]
)

@route.post("/start-meeting", status_code=status.HTTP_201_CREATED)
def start_meeting(meeting_in: MeetingCreate, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    try:
        # if meeting.agent_id is empty raise error
        if not meeting_in.agentIds:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Meeting agentIds is required and cannot be empty"
            )   
            
        # check if all agents exist
        agents_in_db = session.exec(select(Agents).where(Agents.id.in_(meeting_in.agentIds))).all()
        if len(agents_in_db) != len(meeting_in.agentIds):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more agents not found"
            )   

        # check meeeting status
        if meeting_in.status not in ["active", "paused", "ended"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid meeting status"
            )   
            
        # Check if an active meeting already exists for this user with the identical agents
        active_meetings = session.exec(select(Meeting).where(Meeting.user_id == user.id, Meeting.status == "active")).all()
        for am in active_meetings:
            am_agent_ids = {a.id for a in am.agents}
            requested_agent_ids = set(meeting_in.agentIds)
            if am_agent_ids == requested_agent_ids:
                # Same meeting! Just return it instead of creating a duplicate
                return {"message": "Active meeting already exists", "meeting_id": am.id, "success": True} 

        # Create meeting by passing the agents relationship correctly
        db_meeting = Meeting(user_id=user.id, status=meeting_in.status, agents=list(agents_in_db))
        session.add(db_meeting)
        session.commit()
        return {"message": "Meeting created successfully", "meeting_id": db_meeting.id, "success": True}
    except Exception as e: 
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )       



# check if a user meeting is active
@route.get("/check-active-meeting", status_code=status.HTTP_200_OK)
def check_active_meeting(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    try:
        active_meetings = session.exec(select(Meeting).where(Meeting.user_id == user.id, Meeting.status == "active")).all()
        if active_meetings:
            return {"message": "Active meeting already exists", "meeting_id": active_meetings[0].id, "success": True} 
        return {"message": "No active meeting", "success": False}
    except Exception as e: 
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )           