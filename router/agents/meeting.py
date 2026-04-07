from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from database import get_session
from models import User, Agents, Meeting, MeetingCreate
from sqlmodel import select
from utils.auth import get_current_user
from starlette import status
from utils.agents import parse_agents, build_meeting_graph
from utils.store import active_graphs, meeting_states, meeting_profiles

route = APIRouter(
    prefix="/agents",
    tags=["Start Meeting"]  
)

# In-memory stores for active meeting graphs and state
# These are shared with sockets_manager.py

def restore_meeting_memory(meeting: Meeting):
    """Helper to reconstruct LangGraph and state in memory if it was lost during a server reload."""
    meeting_id = str(meeting.id)
    if meeting_id not in active_graphs:
        print(f"[Memory Restore] Rebuilding LangGraph for meeting {meeting_id} after server restart...")
        profiles = parse_agents(meeting.agents)
        meeting_profiles[meeting_id] = profiles
        active_graphs[meeting_id] = build_meeting_graph(profiles)
        
        if meeting_id not in meeting_states:
            meeting_states[meeting_id] = {
                "messages": [],
                "meeting_id": meeting.id,
                "current_speaker": "",
                "participants": profiles,
                "human_input": None,
                "next_agents": [],
            }
    return meeting_id

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

        # check meeting status
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
                meeting_id = str(am.id)
                
                # If the server restarted, the meeting exists in DB but not in memory (store.py).
                restore_meeting_memory(am)
                return {"message": "Active meeting already exists", "meeting_id": am.id, "success": True} 

        # Create meeting in DB
        db_meeting = Meeting(user_id=user.id, status=meeting_in.status, agents=list(agents_in_db))
        session.add(db_meeting)
        session.commit()

        meeting_id = str(db_meeting.id)

        # Parse agent bios into structured profiles via LLM
        profiles = parse_agents(agents_in_db)
        print("profiles: ", profiles)  
        meeting_profiles[meeting_id] = profiles

        # Build the LangGraph for this meeting
        graph = build_meeting_graph(profiles)
        active_graphs[meeting_id] = graph

        # Initialize the meeting state
        meeting_states[meeting_id] = {
            "messages": [],
            "meeting_id": db_meeting.id,
            "current_speaker": "",
            "participants": profiles,
            "human_input": None,
            "next_agents": [],
        }

        print(f"[Meeting] Built graph for meeting {meeting_id} with agents: {[p['name'] for p in profiles]}")

        return {"message": "Meeting created successfully", "meeting_id": db_meeting.id, "success": True}
    except HTTPException:
        raise
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
            am = active_meetings[0]
            restore_meeting_memory(am) # Fixes memory loss if the page was refreshed after standard reload
            return {"message": "Active meeting already exists", "meeting_id": am.id, "success": True} 
        return {"message": "No active meeting", "success": False}
    except Exception as e: 
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )          
