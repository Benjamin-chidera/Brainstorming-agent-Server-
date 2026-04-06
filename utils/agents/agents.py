from langgraph.graph import START, END, StateGraph
from .agent_state import MeetingState
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage, HumanMessage

# llm = ChatOllama(model="deepseek-r1:8b")
llm = ChatOpenAI(model="gpt-3.5-turbo")

# ──────────────────────────────────────────────
# 1. Agent Node Factory
# ──────────────────────────────────────────────
def make_agent_node(agent_profile: dict):
    """
    Factory: returns a LangGraph node function that is 'bound' to one specific agent.
    Each agent gets its own closure with its own profile baked in.
    """

    async def agent_node(state: MeetingState) -> dict:
        # Build recent conversation context (last 10 messages)
        history = ""
        for m in state.get("messages", [])[-10:]:
            msg_type = getattr(m, "type", "unknown")
            if msg_type == "human":
                sender = "Human"
            elif msg_type == "ai":
                content_str = getattr(m, "content", "")
                # Extract agent name from "[AgentName]: ..." format
                if content_str.startswith("[") and "]: " in content_str:
                    sender = content_str.split("]")[0].strip("[")
                else:
                    sender = "Agent"
            else:
                sender = msg_type
            content = getattr(m, "content", str(m))
            history += f"{sender}: {content}\n"

        participant_names = [p["name"] for p in state.get("participants", [])]

        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are {agent_profile['name']}.
            Role: {agent_profile.get('role', 'Participant')}
            Skills: {', '.join(agent_profile.get('skills', []))}
            Expertise: {', '.join(agent_profile.get('expertise', []))}
            Personality: {', '.join(agent_profile.get('personality_traits', []))}

            You are in a brainstorming meeting with: {', '.join(participant_names)} and a human moderator.

            Recent conversation:
            {history}

            Rules:
            - Stay in character based on your personality traits.
            - Keep responses concise and conversational (2-4 sentences).
            - Build on or respectfully challenge others' ideas.
            - If you genuinely have nothing meaningful to add, respond with exactly: [PASS]
            """),
            ("human", "{input}"),
        ])

        # Respond to the most recent message (could be from human or another agent)
        messages_so_far = state.get("messages", [])
        if messages_so_far:
            last_msg = messages_so_far[-1]
            input_text = getattr(last_msg, "content", str(last_msg))
        else:
            input_text = state.get("human_input") or "What are your thoughts?"

        chain = prompt | llm | StrOutputParser()
        response = await chain.ainvoke({"input": input_text})

        # If agent passes, don't add a message
        if "[PASS]" in response:
            return {"current_speaker": agent_profile["name"]}

        return {
            "messages": [AIMessage(content=f"[{agent_profile['name']}]: {response}")],
            "current_speaker": agent_profile["name"],
        }

    # Name the function for debugging
    agent_node.__name__ = f"agent_{agent_profile.get('name', 'unknown')}"
    return agent_node


# ──────────────────────────────────────────────
# 2. Router Node Factory
# ──────────────────────────────────────────────
def make_router_node(agent_profiles: list[dict]):
    """
    Decides which agent should speak first:
    - If the human mentioned an agent by name → that agent goes first
    - Otherwise → LLM picks the most relevant agent based on expertise
    """

    async def router(state: MeetingState) -> dict:
        human_input = state.get("human_input") or ""
        agent_names = [p["name"] for p in agent_profiles]

        # Find which agent name appears first in the message (the one being directly addressed)
        addressed_agent = None
        earliest_pos = len(human_input) + 1
        for name in agent_names:
            pos = human_input.lower().find(name.lower())
            if pos != -1 and pos < earliest_pos:
                earliest_pos = pos
                addressed_agent = name

        if addressed_agent:
            remaining = [n for n in agent_names if n != addressed_agent]
            return {
                "current_speaker": addressed_agent,
                "next_agents": remaining,
            }

        # No direct mention → use LLM to pick the best first responder
        agent_descriptions = "\n".join(
            f"- {p['name']}: {p.get('role', 'Participant')} (expertise: {', '.join(p.get('expertise', []))})"
            for p in agent_profiles
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are a meeting moderator. Given the human's message,
            decide which agent should respond first based on their expertise and relevance.

            Available agents:
            {agent_descriptions}

            Respond with ONLY the agent's name. Nothing else."""),
            ("human", "{input}"),
        ])

        chain = prompt | llm | StrOutputParser()
        first_speaker = await chain.ainvoke({"input": human_input})
        first_speaker = first_speaker.strip()

        # Validate — fallback to first agent if LLM returns garbage
        if first_speaker not in agent_names:
            first_speaker = agent_names[0]

        remaining = [n for n in agent_names if n != first_speaker]
        return {
            "current_speaker": first_speaker,
            "next_agents": remaining,
        }

    return router


# ──────────────────────────────────────────────
# 3. Follow-up Check Node
# ──────────────────────────────────────────────
def make_followup_check():
    """
    After an agent speaks, check if there are more agents that might want to chime in.
    Pops the next agent from next_agents so they get a turn.
    """

    async def followup_check(state: MeetingState) -> dict:
        remaining = list(state.get("next_agents", []))

        if remaining:
            next_speaker = remaining.pop(0)
            return {
                "current_speaker": next_speaker,
                "next_agents": remaining,
            }

        # No more agents to check
        return {
            "current_speaker": "human",
            "next_agents": [],
        }

    return followup_check


# ──────────────────────────────────────────────
# 4. Graph Builder
# ──────────────────────────────────────────────
def _normalize_node_name(name: str) -> str:
    """Convert agent name to a valid LangGraph node name."""
    return name.lower().replace(" ", "_")


def build_meeting_graph(agent_profiles: list[dict]):
    """
    Build a complete LangGraph for one meeting session.
    Called once when a meeting starts. Returns a compiled StateGraph.
    """

    graph = StateGraph(MeetingState)

    # --- Add all nodes ---
    graph.add_node("router", make_router_node(agent_profiles))
    graph.add_node("followup_check", make_followup_check())

    # One node per agent
    agent_node_names = []
    name_to_node = {}
    for profile in agent_profiles:
        node_name = _normalize_node_name(profile["name"])
        agent_node_names.append(node_name)
        name_to_node[profile["name"]] = node_name
        graph.add_node(node_name, make_agent_node(profile))

    # --- Edges ---

    # START → router (always)
    graph.add_edge(START, "router")

    # Router → correct agent (conditional based on current_speaker)
    def route_to_agent(state: MeetingState) -> str:
        speaker = state.get("current_speaker", "")
        node = name_to_node.get(speaker)
        if node and node in agent_node_names:
            return node
        return agent_node_names[0]  # fallback

    graph.add_conditional_edges(
        "router",
        route_to_agent,
        {name: name for name in agent_node_names},
    )

    # Each agent → followup_check
    for node_name in agent_node_names:
        graph.add_edge(node_name, "followup_check")

    # Follow-up check → next agent OR end
    # followup_check already popped the next speaker into current_speaker,
    # so we route based on current_speaker (not next_agents which is already emptied).
    def followup_route(state: MeetingState) -> str:
        current_speaker = state.get("current_speaker", "")
        node = name_to_node.get(current_speaker)
        if node and node in agent_node_names:
            return node
        return "__end__"

    destinations = {name: name for name in agent_node_names}
    destinations["__end__"] = END
    graph.add_conditional_edges("followup_check", followup_route, destinations)

    return graph.compile()