import socketio
import asyncio
from http import cookies
from utils.auth import verify_token

# Create a Socket.IO asynchronous server
# cors_allowed_origins=[] disables Socket.IO's internal CORS handling so FastAPI's CORSMiddleware does it.
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=[])

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
                        except (IndexError, ValueError):
                            await sio.emit("chat_update", {"sender": "Agent", "text": content}, room=room)
                    else:
                        await sio.emit("chat_update", {"sender": "Agent", "text": content}, room=room)
                        
                current_msg_count = new_msgs_count
                
        # Update stored state with the final result
        meeting_states[meeting_id] = final_state
        print(f"[Socket] ✅ AI LangGraph finished for room {room}")
                    
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
