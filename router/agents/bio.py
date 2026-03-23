from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session
from database import get_session
from models import User
from utils.auth import get_current_user
from pydantic import BaseModel
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

bio = APIRouter(
    prefix="/agents",
    tags=["Agent Bio"]
)

@bio.post("/bio")
def agent_setup(bios: List[str], user: User = Depends(get_current_user), session: Session = Depends(get_session)):

    message = [
        {"role": "system", "content": """
        You are the "Council Architect." Your goal is to take a raw, unstructured description of an AI agent and extract specific parameters. 

        **Rules:**
        1. **Deduce Missing Info:** If the user doesn't provide a gender, default to "Neutral". If they don't provide years of experience, estimate based on the "Seniority" implied (e.g., "Expert" = 10+ years).
        2. **Standardize Tone:** Convert descriptive adjectives into a list of "Personality Traits."
        3. **Enhance Expertise:** If a user says "he knows Python," expand that into relevant professional tags like "Backend Development," "Scripting," "Data Science."
        4. **Output Format:** Return ONLY a valid JSON object. No prose. No conversational filler.

        ### JSON Schema Requirement:
        {{
        "identity": {{
            "name": string,
            "gender": "Male" | "Female" | "Non-binary" | "Neutral",
            "years_of_experience": number
        }},
        "professional_profile": {{
            "role": string,
            "expertise_tags": string[],
            "primary_language_model_vibe": "Professional" | "Casual" | "Academic" | "Witty" | "Stoic"
        }},
        "persona": {{
            "traits": string[],
            "communication_style": string
        }}
        }}
        
        """},
        {"role": "user", "content": "{bio}"}
    ]

    model = ChatOllama(model="deepseek-r1:8b")

    chat = ChatPromptTemplate.from_messages(message)
     
    chain = chat | model | StrOutputParser()  
    
    responses = chain.batch([{"bio": b} for b in bios])
    return {"messages": responses}   