from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from sqlmodel import Session
from database import engine
from models import Meeting
import os

llm = ChatOpenAI(model="gpt-3.5-turbo")

def _format_history(messages: list) -> str:
    history = ""
    for m in messages:
        if isinstance(m, HumanMessage):
            history += f"Human: {m.content}\n"
        elif isinstance(m, AIMessage):
            history += f"Agent: {m.content}\n"
    return history

def _save_summary_to_db(meeting_id: int, summary: str, agent_name: str = "System"):
    try:
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            if meeting:
                meeting.summary = summary
                session.add(meeting)
                session.commit()
                print(f"[Summary] Meeting {meeting_id} summarized by {agent_name} and saved.")
    except Exception as e:
        print(f"[Summary] Failed to save summary for meeting {meeting_id}: {e}")

async def summarize_meeting(meeting_id: int, messages: list) -> str:
    """Generic summarization used as a fallback (e.g. from end_meeting)."""
    if not messages:
        return "No conversation to summarize."

    history = _format_history(messages)

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a professional meeting secretary. Summarize the following meeting discussion into a concise paragraph focusing on key decisions and action items."),
        ("human", "{history}")
    ])

    chain = prompt | llm | StrOutputParser()
    summary = await chain.ainvoke({"history": history})
    _save_summary_to_db(meeting_id, summary)
    return summary


async def agent_summarize_meeting(agent_profile: dict, meeting_id: int, messages: list) -> str:
    """
    Has a specific agent summarize the meeting in their own voice and persona.
    The summary is saved to the Meeting record in the DB.
    """
    if not messages:
        return "There's no conversation to summarize yet."

    history = _format_history(messages)
    agent_name = agent_profile.get("name", "Agent")
    role = agent_profile.get("role", "Participant")
    skills = ", ".join(agent_profile.get("skills", []))
    expertise = ", ".join(agent_profile.get("expertise", []))
    personality = ", ".join(agent_profile.get("personality_traits", []))

    prompt = ChatPromptTemplate.from_messages([
        ("system", f"""You are {agent_name}, a {role}.
Skills: {skills}
Expertise: {expertise}
Personality: {personality}

You have been asked to summarize the meeting. Write the summary in your own natural voice, staying true to your character and expertise. Be professional yet personal — highlight key decisions, action items, and important points. Address the group as if you are speaking in the meeting right now. Keep it concise (3-5 sentences)."""),
        ("human", "Here is the full meeting transcript:\n\n{history}\n\nPlease give your summary of this meeting.")
    ])

    chain = prompt | llm | StrOutputParser()
    summary = await chain.ainvoke({"history": history})
    _save_summary_to_db(meeting_id, summary, agent_name)
    return summary
