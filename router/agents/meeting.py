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

def restore_meeting_memory(meeting: Meeting, user: User = None):
    """Helper to reconstruct LangGraph and state in memory if it was lost during a server reload."""
    meeting_id = str(meeting.id)
    if meeting_id not in active_graphs:
        print(f"[Memory Restore] Rebuilding LangGraph for meeting {meeting_id} after server restart...")
        profiles = parse_agents(meeting.agents)
        meeting_profiles[meeting_id] = profiles
        active_graphs[meeting_id] = build_meeting_graph(profiles)
        
        if meeting_id not in meeting_states:
            human_name = "User"
            if user:
                human_name = user.full_name or user.email.split("@")[0]
            elif meeting.user:
                human_name = meeting.user.full_name or meeting.user.email.split("@")[0]

            meeting_states[meeting_id] = {
                "messages": [],
                "meeting_id": meeting.id,
                "current_speaker": "",
                "participants": profiles,
                "human_input": None,
                "human_name": human_name,
                "next_agents": [],
                "agenda_set": False,
                "waiting_for": None,
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
                restore_meeting_memory(am, user)
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
        human_name = meeting_in.userName
        if not human_name or not human_name.strip():
            human_name = user.full_name or user.email.split("@")[0]
            
        meeting_states[meeting_id] = {
            "messages": [],
            "meeting_id": db_meeting.id,
            "current_speaker": "",
            "participants": profiles,
            "human_input": None,
            "human_name": human_name,
            "next_agents": [],
            "agenda_set": False,
            "waiting_for": None,
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
            restore_meeting_memory(am, user) # Fixes memory loss if the page was refreshed after standard reload
            return {"message": "Active meeting already exists", "meeting_id": am.id, "success": True} 
        return {"message": "No active meeting", "success": False}
    except Exception as e: 
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )          

@route.delete("/delete-meeting/{meeting_id}", status_code=status.HTTP_200_OK)
async def delete_meeting(meeting_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    try:
        meeting = session.get(Meeting, meeting_id)
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found"
            )

        if meeting.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to delete this meeting"
            )

        # Cancel autonomous conversation loop and clean up memory
        mid_str = str(meeting_id)
        from sockets_manager import _cancel_continuation, _muted_agents, sio
        _cancel_continuation(mid_str)
        _muted_agents.pop(mid_str, None)

        active_graphs.pop(mid_str, None)
        meeting_states.pop(mid_str, None)
        meeting_profiles.pop(mid_str, None)

        session.delete(meeting)
        session.commit()

        # Notify all clients in the room that the meeting was deleted
        await sio.emit("meeting_ended", {}, room=mid_str)

        return {"message": "Meeting deleted successfully", "success": True}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
