from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated

class MeetingState(TypedDict):
    messages: Annotated[list, add_messages]  # full conversation history
    meeting_id: int
    current_speaker: str                     # which agent/human has the floor
    participants: list[dict]                 # parsed agent profiles
    human_input: str | None                  # latest message from human
    human_name: str                          # user's real name or email
    next_agents: list[str]                   # agents that still need to be checked for follow-up
    agenda_set: bool                         # True once an agent has asked the human for today's agenda
    waiting_for: str | None                  # name of person expected to answer (human name or agent name)