from models import Agents
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

# llm = ChatOllama(model="deepseek-r1:8b")  
llm = ChatOpenAI(model="gpt-3.5-turbo")

def parse_agent_bio(bio: str) -> dict:
    """Extract structured info from a free-text agent bio."""
    
    prompt = [
        ("system", """You are an expert at extracting structured information from bio. Return a JSON object with the following keys: 

        Return this exact structure:
            {{
                "name": "extracted name",
                "role": "extracted role/title",
                "experience_years": <number or null>,
                "skills": ["skill1", "skill2"],
                "expertise": ["domain1", "domain2"],
                "personality_traits": ["trait1", "trait2"],
                "other": "any other relevant info"
            }}
        
        Return ONLY the JSON object, no explanation.
        """),
        ("human", "{bio}"),
    ]

    prompt = ChatPromptTemplate.from_messages(prompt)
    chain = prompt | llm | JsonOutputParser()
    return chain.invoke({"bio": bio})  

def parse_agents(agents: list[Agents]):
    """ Parse all agent bios into structured profiles."""
    profiles = []

    for agent in agents:
        profile = parse_agent_bio(agent.bio)
        # Carry the DB gender field directly so TTS can pick the right voice
        profile["gender"] = (agent.gender or "").strip().lower()
        profile["id"] = agent.id
        profiles.append(profile)

        print("profiles: ", profiles)
    return profiles
