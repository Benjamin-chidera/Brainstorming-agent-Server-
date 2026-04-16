# Project Overview
**BrainStorming-Agents (Server)** is the robust Python backend service responsible for orchestrating interactive AI-driven brainstorming sessions.

### What it does and key features
- **AI Orchestration**: Employs LangChain and LangGraph to manage conversational flows between diverse LLM instances.
- **Real-time Streaming**: Utilizes Socket.IO to broadcast internal agent thoughts, state transitions, and text directly to connected clients.
- **Persistent Memory**: Saves council traits, agent bios, and meeting logs into a SQL database.

## Tech Stack
- **Web Framework**: FastAPI, Uvicorn
- **AI Framework**: LangChain, LangGraph, OpenAI API, Ollama
- **Database ORM**: SQLModel
- **Real-time**: python-socketio
- **Auth**: python-jose (JWT)

## Project Structure
```text
Server/
├── router/          # FastAPI route controllers
├── models/          # SQLModel database schemas
├── utils/
│   └── agents/      # LLM logic and LangGraph configurations
├── database.py      # Database connection setup
├── main.py          # FastAPI application entry point
├── sockets_manager.py # Socket.IO server setup
└── requirements.txt # Python dependencies
```

## Description
The primary role of the backend is spawning and mediating context-aware AI entities holding distinct views. Instead of prompting a single LLM, the Server dynamically constructs LangGraph logic loops based on user-provided setups. It securely handles API routing, manages SQL database actions, and emits live WebSocket events representing the ongoing brainstorming topics.

## Environment Variables
Create a `.env` file in the `Server/` directory and include the necessary values:
```env
# Required for Langchain integrations
OPENAI_API_KEY=your_openai_api_key_here

# Optional overrides/defaults
# DATABASE_URL=sqlite:///./agents.db
# JWT_SECRET_KEY=your_secret_key
```

## Run Backend
1. Navigate to the `Server` directory:
   ```bash
   cd Server
   ```
2. Set up and activate a Python Virtual Environment:
   ```bash
   uv venv
   source .venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   uv add -r requirements.txt
   ```
4. Start the FastAPI server:
   ```bash
   fastapi dev main.py
   ```
   _(The backend will run on http://localhost:8000)_

## API Endpoints
Core REST routes prefixed by `/api/v1`:
- **`POST /bio`**: Define specific agent traits and configurations.
- **`POST /create_council`**: Aggregate agents into a distinct council setup.
- **`POST /meeting`**: Initialize an automated topic session.
- **`GET /live_meeting_room`**: Fetch current runtime status.
- **Auth Routes**: Standard JWT user authentication paths.

_Detailed schema and endpoint documentation is available via the automatic Swagger UI at `http://localhost:8000/docs` while the server is running._

## Key Features
- **Dynamic Meeting Engine**: Fully automated conversational graphs generating organic brainstorming outputs.
- **Secure Isolation**: Built-in JWT authentication ensures user sessions and AI configurations remain private.
- **Event-driven WebSockets**: Real-time pushing of markdown segments directly from the LLM outputs.

## Testing
- **Backend Testing**: Powered by `pytest`. Run all underlying server-side tests inside the `Server/` directory using:
  ```bash
  pytest
  ```

## Author
_Your Name_

## License
MIT License
