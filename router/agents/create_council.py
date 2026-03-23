from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session
from database import get_session
from models import User, Agents, AgentsCreate
from utils.auth import get_current_user

route = APIRouter(
    prefix="/agents",
    tags=["Create Council"]
)

@route.post("/create-council")
def create_council(agents: list[AgentsCreate], user: User = Depends(get_current_user), session: Session = Depends(get_session)):

    try:
        for agent_data in agents:
            # Create a database model instance from the input data
            db_agent = Agents.model_validate(agent_data)
            db_agent.user_id = user.id
            session.add(db_agent)
        
        session.commit()
        return {"message": "Council created successfully"}
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )   
