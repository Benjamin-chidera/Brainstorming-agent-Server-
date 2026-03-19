from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# auth  
from router import auth
# agent-setup
from router import agents

from database import create_db_and_tables
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if they don't exist
    create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ],
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],
)

# Include the router with prefix and tags
app.include_router(auth, prefix="/api/v1")
app.include_router(agents.bio, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"Hello": "World"}   