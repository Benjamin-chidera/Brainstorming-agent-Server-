import socketio
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
        sio.enter_room(sid, room)
        print(f"[Socket] Client {sid} joined meeting room: {room}")
        
        await sio.emit(
            "system_message", 
            {"text": f"Connected securely to meeting {room}!"}, 
            room=room
        )

# @sio.event
# async def user_message(sid, data):
#     """
#     Triggered when the user sends a message in the meeting.
#     """
#     meeting_id = data.get("meeting_id")
#     text = data.get("text")
#     room = str(meeting_id)
    
#     print(f"[Socket] Message from {sid} in room {room}: {text}")
    
#     # Broadcast the user's message back to the room so it shows up in the UI
#     await sio.emit("chat_update", {"sender": "User", "text": text}, room=room)
    
#     # -------------------------------------------------------------------------------- #
#     # TODO: LangGraph Integration goes here!                                           #
#     # 1. You will take the `text` (user's input).                                      #
#     # 2. Extract agent configuration (from your LangGraph logic & bio JSON).           #
#     # 3. Pass the input + context to the LLM.                                          #
#     # 4. Once the LLM generates the response, emit it back to the room like below:     #
#     #                                                                                  #
#     # response_text = await langgraph_agent_chain(text, agent_json_context)            #
#     # await sio.emit("chat_update", {"sender": "AI Agent", "text": response_text}, room=room) #
#     # -------------------------------------------------------------------------------- #

