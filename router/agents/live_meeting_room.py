from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session
from database import get_session
from models import User, Meeting
from sqlmodel import select
from utils.auth import get_current_user

# We import these from meeting.py because that's where the meetings are started
from .meeting import meeting_states, meeting_profiles

route = APIRouter(
    prefix="/meeting",
    tags=["Live Meeting Room"]
)

@route.get("/{meeting_id}", status_code=status.HTTP_200_OK)
def get_live_room_state(meeting_id: str, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """
    Called by the frontend when a user enters the live meeting room page.
    Returns the current state (messages, participants) to populate the initial UI.
    """
    
    # 1. Verify the meeting exists in the DB and belongs to the user
    meeting = session.exec(select(Meeting).where(Meeting.id == int(meeting_id), Meeting.user_id == user.id)).first()
    
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # 2. Get the current live state from memory (messages, next speaker, etc.)
    live_state = meeting_states.get(meeting_id)
    profiles = meeting_profiles.get(meeting_id)

    if not live_state:
        # If not in memory but in DB, it might be an 'ended' meeting or need re-init
        return {
            "success": True,
            "status": meeting.status,
            "messages": [],
            "participants": profiles or [],
            "is_live": False
        }

    return {
        "success": True,
        "status": meeting.status,
        "is_live": True,
        "state": {
            "messages": live_state.get("messages", []),
            "current_speaker": live_state.get("current_speaker", ""),
            "participants": profiles,
        }
    }
