from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated

class MeetingState(TypedDict):
    messages: Annotated[list, add_messages]  # full conversation history
    meeting_id: int
    current_speaker: str                     # which agent/human has the floor
    participants: list[dict]                 # parsed agent profiles
    human_input: str | None                  # latest message from human
    next_agents: list[str]                   # agents that still need to be checked for follow-up