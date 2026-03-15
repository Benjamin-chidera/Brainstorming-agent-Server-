import random
import string
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from config import settings
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select
from database import get_session
from models import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/otp/verify")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        # Default to 7 days (10080 minutes) if not in settings
        expire = datetime.now(timezone.utc) + timedelta(minutes=getattr(settings, "REFRESH_TOKEN_EXPIRE_MINUTES", 10080))
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))

def verify_token(token: str):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(
    request: Request, # Inject the Request object
    session: Session = Depends(get_session)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )

    # 1. Get the token from the cookie instead of the Header
    token = request.cookies.get("access_token")
    
    if not token:
        raise credentials_exception

    # 2. Verify the token (Your existing verify_token function)
    payload = verify_token(token)
    
    # Check if it's the wrong type (we only want 'access' tokens here)
    if payload is None or payload.get("type") == "refresh":
        raise credentials_exception
    
    email: str = payload.get("sub")
    if email is None:
        raise credentials_exception
    
    # 3. Database lookup
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        raise credentials_exception
        
    return user