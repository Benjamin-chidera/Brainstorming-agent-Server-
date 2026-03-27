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

        # check if agent already exists 
        for agent_data in agents:
            existing_agent = session.query(Agents).filter(Agents.user_id == user.id, Agents.gender == agent_data.gender, Agents.bio == agent_data.bio, Agents.accent == agent_data.accent, Agents.avatarUrl == agent_data.avatarUrl, Agents.tone == agent_data.tone, Agents.voice == agent_data.voice).first()
            if existing_agent:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Agent already exists"
                )   
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


@route.get("/get-council")
def get_council(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    try:
        council = session.query(Agents).filter(Agents.user_id == user.id).all()
        return council
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )   


@route.delete("/delete-council/{id}")
def delete_council(id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    try:
        council = session.query(Agents).filter(Agents.id == id).first()
        if not council:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Council not found"
            )
        session.delete(council)
        session.commit()
        return {"message": "Council deleted successfully"}
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )   


@route.patch("/update-a-council/{id}")
def update_a_council(id: int, agent_data: AgentsCreate, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    try:
        council = session.query(Agents).filter(Agents.id == id).first()
        if not council:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Council not found"
            )
        council.gender = agent_data.gender
        council.bio = agent_data.bio
        council.accent = agent_data.accent
        council.avatarUrl = agent_data.avatarUrl
        council.tone = agent_data.tone
        council.voice = agent_data.voice
        session.commit()
        return {"message": "Council updated successfully"}
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )   