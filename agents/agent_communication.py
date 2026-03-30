from models import Agents
from langgraph.graph import START, END, StateGraph

def get_agents(agents: list[Agents]):
    # pass agent data to langgraph  

    for agent in agents:
        print("agent bio: ", agent.bio)    
