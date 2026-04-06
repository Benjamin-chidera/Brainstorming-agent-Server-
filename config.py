from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite:///./database.db"
    
    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200 # 30 days
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080 # 7 days
    
    # Email (FastAPI-Mail)
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.gmail.com" 
    MAIL_FROM_NAME: str = "Council.ai"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    USE_CREDENTIALS: bool = True
    VALIDATE_CERTS: bool = True

    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")

    # GitHub OAuth
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    GITHUB_REDIRECT_URI: str = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/auth/github/callback")

    # Frontend URL (for OAuth redirect after login)
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")

    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
