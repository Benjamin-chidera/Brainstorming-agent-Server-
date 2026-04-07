import socketio
import asyncio
from http import cookies
from utils.auth import verify_token

# Create a Socket.IO asynchronous server
# cors_allowed_origins=[] disables Socket.IO's internal CORS handling so FastAPI's CORSMiddleware does it.
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=[])

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

async def _emit_tts(text: str, sender: str, room: str, meeting_id: str):
    """Generate TTS for an agent response and emit agent_audio to the room."""
    try:
        from utils.agents.agent_tts import synthesize_speech
        voice = _voice_for_agent(sender, meeting_id)
        audio_b64 = await asyncio.to_thread(synthesize_speech, text, voice)
        if audio_b64:
            await sio.emit("agent_audio", {"sender": sender, "audio": audio_b64}, room=room)
    except Exception as e:
        print(f"[TTS] Failed for {sender}: {e}")


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
    Rotates through agents, giving each a chance to speak.
    Runs until cancelled (by a new human message or meeting end).
    """
    import random
    from utils.store import meeting_states, meeting_profiles
    from utils.agents.agents import run_single_agent
    from langchain_core.messages import AIMessage

    # Brief pause before agents continue on their own
    await asyncio.sleep(4)

    last_speaker: str | None = None

    while True:
        profiles = meeting_profiles.get(meeting_id, [])
        state = meeting_states.get(meeting_id)
        if not profiles or state is None:
            break

        # Pick an agent that isn't the one who just spoke, with some randomness
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
            asyncio.create_task(_emit_tts(response, name, room, meeting_id))

            # Persist the message into the shared state
            updated_state = dict(state)
            updated_state["messages"] = list(state.get("messages", [])) + [
                AIMessage(content=f"[{name}]: {response}")
            ]
            meeting_states[meeting_id] = updated_state
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

    print(f"[Socket] Message from {sid} in room {room}: {text}")

    # Broadcast the user's message back to the room so it shows up in the UI
    await sio.emit("chat_update", {"sender": "You", "text": text}, room=room)
    
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
                            asyncio.create_task(_emit_tts(response_text, name, room, meeting_id))
                        except (IndexError, ValueError):
                            await sio.emit("chat_update", {"sender": "Agent", "text": content}, room=room)
                            asyncio.create_task(_emit_tts(content, "Agent", room, meeting_id))
                    else:
                        await sio.emit("chat_update", {"sender": "Agent", "text": content}, room=room)
                        asyncio.create_task(_emit_tts(content, "Agent", room, meeting_id))
                        
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
    from models import Meeting
    from sqlmodel import Session
    from datetime import datetime, timezone
    
    try:
        with Session(engine) as session:
            meeting_int_id = int(meeting_id)
            meeting = session.get(Meeting, meeting_int_id)
            if meeting:
                meeting.status = "ended"
                meeting.ended_at = datetime.now(timezone.utc)
                session.add(meeting)
                session.commit()
    except Exception as e:
        print(f"[Socket] Error ending meeting {meeting_id} in DB: {e}")

    # Clean up memory state
    from utils.store import active_graphs, meeting_states, meeting_profiles
    active_graphs.pop(meeting_id, None)
    meeting_states.pop(meeting_id, None)
    meeting_profiles.pop(meeting_id, None)

    print(f"[Socket] Meeting {meeting_id} ended by {sid}")
