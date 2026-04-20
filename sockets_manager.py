import socketio
import asyncio
from http import cookies
from utils.auth import verify_token
from utils.agents.vector_store import sync_message_to_pinecone
from utils.agents.summarizer import summarize_meeting, agent_summarize_meeting
from sqlmodel import Session, select
from database import engine
from models import Message, Meeting
from datetime import datetime, timezone

# Create a Socket.IO asynchronous server
# cors_allowed_origins=[] disables Socket.IO's internal CORS handling so FastAPI's CORSMiddleware does it.
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=[])

# ── Per-meeting muted agent sets ──────────────────────────────────────────────
# Maps meeting_id (str) → set of muted agent IDs (str).
# Agents in this set will be skipped during TTS / audio emission.
_muted_agents: dict[str, set[str]] = {}

# OpenAI voices split by gender
_MALE_VOICES   = ["onyx", "echo", "fable"]
_FEMALE_VOICES = ["nova", "shimmer", "alloy"]

def _voice_for_agent(name: str, meeting_id: str) -> str:
    from utils.store import meeting_profiles
    profiles = meeting_profiles.get(meeting_id, [])
    agent = next((p for p in profiles if p.get("name", "").lower() == name.lower()), None)
    gender = (agent.get("gender", "") if agent else "").lower()

    if "female" in gender or gender == "f":
        pool = _FEMALE_VOICES
    elif "male" in gender or gender == "m":
        pool = _MALE_VOICES
    else:
        pool = _MALE_VOICES + _FEMALE_VOICES  # unknown gender → any

    return pool[hash(name) % len(pool)]

def _save_message_to_db(meeting_id: str, sender_type: str, sender_name: str, content: str):
    """Save a single message to the SQL database for ephemeral persistence."""
    try:
        with Session(engine) as session:
            new_msg = Message(
                meeting_id=int(meeting_id),
                sender_type=sender_type,
                sender_name=sender_name,
                content=content,
                created_at=datetime.now(timezone.utc)
            )
            session.add(new_msg)
            session.commit()
    except Exception as e:
        print(f"[DB Save] Failed to save message: {e}")

async def _emit_tts(text: str, sender: str, room: str, meeting_id: str):
    """Generate TTS for an agent response and emit agent_audio to the room.
    
    Skips synthesis entirely if the agent is muted for this meeting.
    """
    # Check if sender is muted (match by name against muted agent ID set)
    muted = _muted_agents.get(meeting_id, set())
    if muted:
        from utils.store import meeting_profiles
        profiles = meeting_profiles.get(meeting_id, [])
        agent = next((p for p in profiles if p.get("name", "").lower() == sender.lower()), None)
        agent_id = str(agent.get("id", "")) if agent else ""
        if agent_id and agent_id in muted:
            print(f"[TTS] Skipping muted agent: {sender} ({agent_id})")
            return
    try:
        from utils.agents.agent_tts import synthesize_speech
        voice = _voice_for_agent(sender, meeting_id)
        audio_b64 = await asyncio.to_thread(synthesize_speech, text, voice)
        if audio_b64:
            await sio.emit("agent_audio", {"sender": sender, "audio": audio_b64}, room=room)
    except Exception as e:
        print(f"[TTS] Failed for {sender}: {e}")

# ── Sequential TTS Queue ──────────────────────────────────────────────────────
_tts_queues: dict[str, asyncio.Queue] = {}
_tts_tasks: dict[str, asyncio.Task] = {}

async def _tts_worker(meeting_id: str):
    queue = _tts_queues[meeting_id]
    try:
        while True:
            text, sender, room = await queue.get()
            await _emit_tts(text, sender, room, meeting_id)
            queue.task_done()
    except asyncio.CancelledError:
        pass

def _enqueue_tts(text: str, sender: str, room: str, meeting_id: str):
    if meeting_id not in _tts_queues:
        _tts_queues[meeting_id] = asyncio.Queue()
        _tts_tasks[meeting_id] = asyncio.create_task(_tts_worker(meeting_id))
    _tts_queues[meeting_id].put_nowait((text, sender, room))

def _clear_tts_queue(meeting_id: str):
    if meeting_id in _tts_queues:
        q = _tts_queues[meeting_id]
        while not q.empty():
            try:
                q.get_nowait()
                q.task_done()
            except asyncio.QueueEmpty:
                break



# ── Autonomous conversation loop ──────────────────────────────────────────────
# One background task per active meeting. Keeps agents talking when the human
# is silent. Cancelled and restarted whenever the human sends a message.
_continuation_tasks: dict[str, asyncio.Task] = {}


def _cancel_continuation(meeting_id: str):
    task = _continuation_tasks.pop(meeting_id, None)
    if task and not task.done():
        task.cancel()


def _start_continuation(meeting_id: str, room: str):
    _cancel_continuation(meeting_id)
    task = asyncio.create_task(_autonomous_conversation(meeting_id, room))
    _continuation_tasks[meeting_id] = task


async def _autonomous_conversation(meeting_id: str, room: str):
    """
    Keeps the meeting alive when the human is idle.
    - First run: if agenda hasn't been set yet, one agent asks for the agenda.
    - If an agent asked a question, respects waiting_for before resuming free talk.
    - Rotates through agents, giving each a chance to speak.
    Runs until cancelled (by a new human message or meeting end).
    """
    import random
    from utils.store import meeting_states, meeting_profiles
    from utils.agents.agents import run_single_agent, _extract_question_target
    from langchain_core.messages import AIMessage

    # Brief pause before agents continue on their own
    await asyncio.sleep(4)

    profiles = meeting_profiles.get(meeting_id, [])
    state = meeting_states.get(meeting_id)
    if not profiles or state is None:
        return

    # ── Step 1: Ask for agenda after first introduction round ──────────────────
    # If the agenda hasn't been set yet and there are already messages (agents have spoken),
    # have the first agent in the list ask the human what today's agenda is.
    if not state.get("agenda_set", False) and state.get("messages"):
        first_agent = profiles[0]
        name = first_agent["name"]
        human_name = state.get("human_name", "everyone")
        agenda_question = (
            f"Before we dive in — {human_name}, what's the agenda for today's meeting? "
            f"What would you like us to focus on?"
        )
        await sio.emit("chat_update", {"sender": name, "text": agenda_question}, room=room)
        _enqueue_tts(agenda_question, name, room, meeting_id)
        sync_message_to_pinecone(meeting_id, name, agenda_question)
        _save_message_to_db(meeting_id, "agent", name, agenda_question)

        updated = dict(state)
        updated["messages"] = list(state.get("messages", [])) + [
            AIMessage(content=f"[{name}]: {agenda_question}")
        ]
        updated["agenda_set"] = True
        updated["waiting_for"] = human_name  # wait for human to reply with agenda
        meeting_states[meeting_id] = updated
        # After asking the agenda question, stop — wait for human's response
        return

    # ── Step 2: Free-flowing autonomous conversation ───────────────────────────
    last_speaker: str | None = None

    while True:
        profiles = meeting_profiles.get(meeting_id, [])
        state = meeting_states.get(meeting_id)
        if not profiles or state is None:
            break

        human_name = state.get("human_name", "Human")
        waiting_for = state.get("waiting_for")

        # If waiting for the human to answer, pause and recheck — don't speak over them
        if waiting_for and waiting_for.lower() == human_name.lower():
            await asyncio.sleep(6)
            # Re-read state: if human has responded, waiting_for would be cleared
            state = meeting_states.get(meeting_id)
            if state and state.get("waiting_for", "").lower() == human_name.lower():
                # Still waiting — keep pausing silently
                continue
            # waiting_for was cleared (human responded) → fall through to normal turn
            profiles = meeting_profiles.get(meeting_id, [])
            state = meeting_states.get(meeting_id)
            if not profiles or state is None:
                break
            waiting_for = state.get("waiting_for")

        # If an agent asked another agent a question, let that agent respond first
        if waiting_for:
            agent = next((p for p in profiles if p["name"].lower() == waiting_for.lower()), None)
            if agent:
                name = agent["name"]
                try:
                    response = await run_single_agent(agent, state, continuation=True)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[Continuation] Error for {name}: {e}")
                    await asyncio.sleep(5)
                    continue

                updated = dict(state)
                updated["waiting_for"] = None  # clear — question has been answered

                if response:
                    await sio.emit("chat_update", {"sender": name, "text": response}, room=room)
                    _enqueue_tts(response, name, room, meeting_id)
                    sync_message_to_pinecone(meeting_id, name, response)
                    _save_message_to_db(meeting_id, "agent", name, response)
                    updated["messages"] = list(state.get("messages", [])) + [
                        AIMessage(content=f"[{name}]: {response}")
                    ]
                    # Only detect a NEW question target if this agent wasn't the one answering —
                    # answering agents should not immediately redirect the floor.
                    participant_names = [p["name"] for p in profiles]
                    new_target = _extract_question_target(response, participant_names, human_name)
                    updated["waiting_for"] = new_target
                    last_speaker = name

                meeting_states[meeting_id] = updated
                await asyncio.sleep(random.uniform(3, 5))
                continue
            else:
                # waiting_for target not found — clear it so the loop doesn't stall
                updated = dict(state)
                updated["waiting_for"] = None
                meeting_states[meeting_id] = updated
                state = updated

        # Normal random-turn selection (skip last speaker for variety)
        candidates = [p for p in profiles if p.get("name") != last_speaker]
        if not candidates:
            candidates = profiles
        agent = random.choice(candidates)
        name = agent["name"]

        try:
            response = await run_single_agent(agent, state, continuation=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Continuation] Error for {name}: {e}")
            await asyncio.sleep(5)
            continue

        if response:
            await sio.emit("chat_update", {"sender": name, "text": response}, room=room)
            _enqueue_tts(response, name, room, meeting_id)
            sync_message_to_pinecone(meeting_id, name, response)
            _save_message_to_db(meeting_id, "agent", name, response)

            updated = dict(state)
            updated["messages"] = list(state.get("messages", [])) + [
                AIMessage(content=f"[{name}]: {response}")
            ]
            # Detect if this agent asked someone a question
            participant_names = [p["name"] for p in profiles]
            updated["waiting_for"] = _extract_question_target(response, participant_names, human_name)
            meeting_states[meeting_id] = updated
            last_speaker = name

        # Natural pause between autonomous turns (3–6 seconds)
        await asyncio.sleep(random.uniform(3, 6))


# Wrap it in an ASGI application. We set socketio_path='' because we will
# use FastAPI to mount this application at the '/socket.io' path.
sio_app = socketio.ASGIApp(sio, socketio_path='')

@sio.event
async def connect(sid, environ, auth):
    """
    Triggered when a client connects. The browser automatically sends the 
    HttpOnly cookie which we extract from the ASGI environ.
    """
    cookie_header = environ.get("HTTP_COOKIE", "")
    cookie = cookies.SimpleCookie()
    cookie.load(cookie_header)
    
    access_token = cookie.get("access_token")
    if not access_token:
        print(f"[Socket] Connection rejected (No token): {sid}")
        raise socketio.exceptions.ConnectionRefusedError('Authentication failed: No access token found')
    
    payload = verify_token(access_token.value)
    if not payload or payload.get("type") == "refresh":
        print(f"[Socket] Connection rejected (Invalid token): {sid}")
        raise socketio.exceptions.ConnectionRefusedError('Authentication failed: Invalid token')
    
    email = payload.get("sub")
    if not email:
        print(f"[Socket] Connection rejected (Invalid payload): {sid}")
        raise socketio.exceptions.ConnectionRefusedError('Authentication failed: User not found in token')
    
    # Store the user's email in the socket session so we know who is talking later
    async with sio.session(sid) as session:
        session['email'] = email
        
    print(f"[Socket] Client connected: {sid} (User: {email})")

@sio.event
async def disconnect(sid):
    print(f"[Socket] Client disconnected: {sid}")

@sio.event
async def join_meeting(sid, data):
    """
    Frontend emits this to join a specific meeting room.
    """
    meeting_id = data.get("meeting_id")
    if meeting_id:
        room = str(meeting_id)
        # Add the connection to the specific meeting's room
        await sio.enter_room(sid, room)
        print(f"[Socket] Client {sid} joined meeting room: {room}")
        
        # Import here to avoid circular imports
        from utils.store import meeting_profiles
        
        profiles = meeting_profiles.get(room, [])
        agent_names = [p["name"] for p in profiles]

        await sio.emit(
            "system_message", 
            {
                "text": f"Connected securely to meeting {room}!",
                "agents": agent_names,
            }, 
            room=room
        )

@sio.event
async def user_typing(sid, data):
    """
    Triggered when the user starts typing or activates their mic.
    Cancels the autonomous conversation loop so agents don't interrupt.
    """
    meeting_id = str(data.get("meeting_id", ""))
    if meeting_id:
        _cancel_continuation(meeting_id)
        _clear_tts_queue(meeting_id)
        print(f"[Socket] User typing in room {meeting_id} — autonomous loop paused")


@sio.event
async def user_message(sid, data):
    """
    Triggered when the user sends a message in the meeting.
    Runs the LangGraph to get agent responses and streams them back.
    """
    meeting_id = str(data.get("meeting_id"))
    text = data.get("text", "")
    room = meeting_id

    # Human is speaking — stop autonomous loop immediately
    _cancel_continuation(meeting_id)
    _clear_tts_queue(meeting_id)

    print(f"[Socket] Message from {sid} in room {room}: {text}")

    # Broadcast the user's message back to the room so it shows up in the UI
    await sio.emit("chat_update", {"sender": "You", "text": text}, room=room)
    _save_message_to_db(meeting_id, "human", "You", text)
    
    # Sync to Vector DB (Pinecone) for long-term memory
    sync_message_to_pinecone(meeting_id, "You", text)

    # Check if user is asking an agent to summarize the meeting
    _summarize_keywords = ["summarize", "summary", "recap", "wrap up", "sum up"]
    if any(kw in text.lower() for kw in _summarize_keywords):
        from utils.store import meeting_states, meeting_profiles
        profiles = meeting_profiles.get(meeting_id, [])
        state = meeting_states.get(meeting_id, {})
        messages = state.get("messages", [])

        # Find which agent was addressed by name, fallback to first agent
        text_lower = text.lower()
        addressed_agent = next(
            (p for p in profiles if p["name"].lower() in text_lower),
            profiles[0] if profiles else None
        )

        if addressed_agent:
            agent_name = addressed_agent["name"]
            await sio.emit("agent_typing", {"typing": True}, room=room)
            await sio.emit("system_message", {"text": f"📝 {agent_name} is preparing the meeting summary..."}, room=room)

            summary = await agent_summarize_meeting(addressed_agent, int(meeting_id), messages)

            await sio.emit("agent_typing", {"typing": False}, room=room)
            await sio.emit("chat_update", {"sender": agent_name, "text": summary}, room=room)
            _enqueue_tts(summary, agent_name, room, meeting_id)
            _save_message_to_db(meeting_id, "agent", agent_name, summary)
            sync_message_to_pinecone(meeting_id, agent_name, summary)

            from langchain_core.messages import AIMessage as _AIMessage
            state["messages"] = list(state.get("messages", [])) + [
                _AIMessage(content=f"[{agent_name}]: {summary}")
            ]
            meeting_states[meeting_id] = state
            _start_continuation(meeting_id, room)
            return

    # Import here to avoid circular imports
    from utils.store import active_graphs, meeting_states, meeting_profiles
    print(f"Looking for key: '{meeting_id}' in keys: {list(active_graphs.keys())}")
    
    graph = active_graphs.get(meeting_id)
    if not graph:
        await sio.emit(
            "system_message", 
            {"text": "Meeting graph not initialized. Please start a meeting first."}, 
            room=room
        )
        return

    print("graph: ", graph)

    # Get the current state and update with human input
    from langchain_core.messages import HumanMessage
    state = meeting_states.get(meeting_id, {})
    state["human_input"] = text
    # Human responded — clear any pending question target
    state["waiting_for"] = None
    # Ensure new state fields have defaults
    state.setdefault("agenda_set", False)
    # Add the human's message so agents can see it as the last message in history
    state.setdefault("messages", [])
    state["messages"] = list(state["messages"]) + [HumanMessage(content=text)]

    # Show typing indicator
    await sio.emit("agent_typing", {"typing": True}, room=room)
    
    try:
        print(f"[Socket] 🧠 Running AI LangGraph for room {room}...")
        
        current_msg_count = len(state.get("messages", []))
        final_state = state

        # Stream the graph execution asynchronously so we get realtime updates
        # This prevents having to wait for ALL agents before seeing the first reply.
        async for next_state in graph.astream(state, stream_mode="values"):
            final_state = next_state
            new_msgs_count = len(next_state.get("messages", []))
            
            # If a new message was added during this step
            if new_msgs_count > current_msg_count:
                new_msgs = next_state["messages"][current_msg_count:]
                
                for msg in new_msgs:
                    content = getattr(msg, "content", str(msg))
                    if content.startswith("["):
                        try:
                            name = content.split("]")[0].strip("[")
                            response_text = content.split("]: ", 1)[1] if "]: " in content else content
                            await sio.emit("chat_update", {"sender": name, "text": response_text}, room=room)
                            _enqueue_tts(response_text, name, room, meeting_id)
                            
                            # Sync agent response to Vector DB
                            sync_message_to_pinecone(meeting_id, name, response_text)
                            _save_message_to_db(meeting_id, "agent", name, response_text)
                        except (IndexError, ValueError):
                            await sio.emit("chat_update", {"sender": "Agent", "text": content}, room=room)
                            _enqueue_tts(content, "Agent", room, meeting_id)
                    else:
                        await sio.emit("chat_update", {"sender": "Agent", "text": content}, room=room)
                        _enqueue_tts(content, "Agent", room, meeting_id)
                        
                current_msg_count = new_msgs_count
                
        # Update stored state with the final result
        meeting_states[meeting_id] = final_state
        print(f"[Socket] ✅ AI LangGraph finished for room {room}")

        # Resume autonomous conversation — agents keep talking until human speaks again
        _start_continuation(meeting_id, room)

    except Exception as e:
        print(f"[Socket] Error running graph for meeting {meeting_id}: {e}")
        await sio.emit(
            "system_message",
            {"text": f"Error processing message: {str(e)}"},
            room=room
        )
    finally:
        # Hide typing indicator
        await sio.emit("agent_typing", {"typing": False}, room=room)

@sio.event
async def user_audio(sid, data):
    """
    Triggered when the user sends a voice message.
    """
    meeting_id = str(data.get("meeting_id"))
    audio_data = data.get("audio")
    room = meeting_id
    
    if not audio_data:
        return
        
    print(f"[Socket] Received audio from {sid} in room {room}")
    
    import base64
    import os
    import tempfile
    
    # Try to decode base64 or assume bytes
    if isinstance(audio_data, str):
        if audio_data.startswith("data:audio"):
            audio_data = audio_data.split(",")[1]
        try:
            audio_bytes = base64.b64decode(audio_data)
        except Exception:
            audio_bytes = audio_data.encode('utf-8')
    else:
        # Assuming bytes
        audio_bytes = audio_data

    # Write to a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
        tmp_file.write(audio_bytes)
        temp_path = tmp_file.name

    try:
        # Show that we are transcribing
        await sio.emit("system_message", {"text": "🎙️ Transcribing audio..."}, room=room)
        
        # Run transcription in a separate thread since it's a synchronous blocking function
        from utils.agents.agent_human_voice import transcribe_audio
        text = await asyncio.to_thread(transcribe_audio, temp_path)
        
        if text:
            # Fallback to the regular message processing
            await user_message(sid, {"meeting_id": meeting_id, "text": text})
        else:
            await sio.emit("system_message", {"text": "Could not transcribe audio."}, room=room)
            
    except Exception as e:
        print(f"[Socket] Error processing audio message from {sid}: {e}")
        await sio.emit("system_message", {"text": f"Error transcribing audio: {str(e)}"}, room=room)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@sio.event
async def end_meeting(sid, data):
    meeting_id = str(data.get("meetingId", ""))
    if not meeting_id:
        return

    _cancel_continuation(meeting_id)
    
    # Mark meeting as ended in the database
    from database import engine
    from models import Meeting, Message
    from sqlmodel import Session, select, delete
    from datetime import datetime, timezone
    
    try:
        with Session(engine) as session:
            meeting_int_id = int(meeting_id)
            meeting = session.get(Meeting, meeting_int_id)
            if meeting:
                meeting.status = "ended"
                meeting.ended_at = datetime.now(timezone.utc)
                session.add(meeting)

                # DELETE all messages for this meeting from SQL (they still live in Pinecone)
                statement = select(Message).where(Message.meeting_id == meeting_int_id)
                results = session.exec(statement)
                for msg in results:
                    session.delete(msg)

                session.commit()
                print(f"[Socket] Meeting {meeting_id} ended: SQL messages deleted.")
    except Exception as e:
        print(f"[Socket] Error ending meeting {meeting_id} in DB: {e}")

    # Clean up memory state
    from utils.store import active_graphs, meeting_states, meeting_profiles
    active_graphs.pop(meeting_id, None)
    meeting_states.pop(meeting_id, None)
    meeting_profiles.pop(meeting_id, None)
    _muted_agents.pop(meeting_id, None)  # clear mute state for this meeting
    _clear_tts_queue(meeting_id)
    task = _tts_tasks.pop(meeting_id, None)
    if task:
        task.cancel()
    _tts_queues.pop(meeting_id, None)

    # Notify all room members that the meeting has ended (stops audio/UI on all clients)
    await sio.emit("meeting_ended", {}, room=meeting_id)
    print(f"[Socket] Meeting {meeting_id} ended by {sid}")


@sio.event
async def mute_agent(sid, data):
    """
    Triggered when the user mutes a specific agent.
    The agent's ID is added to the muted set for this meeting so that
    subsequent TTS calls for that agent are suppressed server-side.
    """
    meeting_id = str(data.get("meeting_id", ""))
    agent_id   = str(data.get("agent_id", ""))
    if not meeting_id or not agent_id:
        return

    _muted_agents.setdefault(meeting_id, set()).add(agent_id)
    print(f"[Socket] Agent {agent_id} muted in meeting {meeting_id} by {sid}")


@sio.event
async def unmute_agent(sid, data):
    """
    Triggered when the user unmutes a specific agent.
    Removes the agent from the muted set so TTS resumes for that agent.
    """
    meeting_id = str(data.get("meeting_id", ""))
    agent_id   = str(data.get("agent_id", ""))
    if not meeting_id or not agent_id:
        return

    _muted_agents.get(meeting_id, set()).discard(agent_id)
    print(f"[Socket] Agent {agent_id} unmuted in meeting {meeting_id} by {sid}")
