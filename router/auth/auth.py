from fastapi import APIRouter, Depends, HTTPException, status, Body, Response, Cookie
from sqlmodel import Session, select
from starlette import status
from database import get_session
from models import User, OTP
from utils.auth import create_access_token, create_refresh_token, generate_otp, get_current_user, verify_token
from typing import Optional
from utils.email import send_otp_email


from datetime import datetime, timedelta, timezone
from pydantic import EmailStr, BaseModel
import httpx
from config import settings

class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr 

class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str

auth = APIRouter(
    prefix="/auth",
    tags=["Auth"]   
)

@auth.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    return user


@auth.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,  
    session: Session = Depends(get_session)
):
    try:
        # Check if user exists
        user = session.exec(select(User).where(User.email == data.email)).first()
        if user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")
        
        # Generate OTP
        otp_code = generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        
        # Store OTP with name
        db_otp = OTP(email=data.email, code=otp_code, expires_at=expires_at, full_name=data.full_name)
        session.add(db_otp)
        session.commit()
        
        # Send Email
        await send_otp_email(data.email, otp_code) 
        
        return {"message": "OTP sent to your email"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))    

@auth.post("/login", status_code=status.HTTP_200_OK)
async def login( 
    email: EmailStr = Body(..., embed=True), 
    session: Session = Depends(get_session)
):
   try:
    # Check if user exists
    user = session.exec(select(User).where(User.email == email)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found. Please register first.")
    
    # Generate OTP
    otp_code = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    
    # Store OTP
    db_otp = OTP(email=email, code=otp_code, expires_at=expires_at)
    session.add(db_otp)
    session.commit()
    
    # Send Email
    await send_otp_email(email, otp_code) 
    
    return {"message": "OTP sent to your email"}
   except Exception as e:
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))       

@auth.post("/verify-otp", status_code=status.HTTP_200_OK)
async def verify_otp(
    response: Response,
    data: VerifyOTPRequest,
    session: Session = Depends(get_session)
):
    try:
        # Check OTP
        statement = select(OTP).where(OTP.email == data.email, OTP.code == data.otp, OTP.is_used == False)
        db_otp = session.exec(statement).first()
        
        if not db_otp:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")

        otp_expires_at = db_otp.expires_at
        if otp_expires_at.tzinfo is None:
            otp_expires_at = otp_expires_at.replace(tzinfo=timezone.utc)

        if otp_expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")
        
        # Mark OTP as used
        db_otp.is_used = True
        session.add(db_otp)
        
        # Check if user exists
        user_statement = select(User).where(User.email == data.email)
        user = session.exec(user_statement).first()
        
        if not user:
            if not db_otp.full_name:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User not found and no registration info provided")

            # Register user
            user = User(email=data.email, full_name=db_otp.full_name)
            session.add(user)
        
        session.commit()
        session.refresh(user)
        
        # Generate JWT
        access_token = create_access_token(data={"sub": user.email, "id": user.id})
        refresh_token = create_refresh_token(data={"sub": user.email, "id": user.id})

        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            max_age=1800,
            expires=1800,
            path="/",
            samesite="lax",
            secure=True,
        )
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            max_age=10080 * 60,
            expires=10080 * 60,
            path="/",
            samesite="lax",
            secure=True,
        )
        return {
           "message": "OTP verified successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))   

@auth.post("/logout", status_code=status.HTTP_200_OK)   
async def logout(response: Response):
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"message": "Logged out successfully"}

@auth.post("/refresh", status_code=status.HTTP_200_OK   )
async def refresh_access_token(
    response: Response,
    refresh_token: Optional[str] = Cookie(None),
    body_refresh_token: Optional[str] = Body(None, alias="refresh_token")
):
    token = refresh_token or body_refresh_token
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")
    
    payload = verify_token(token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
        
    email: Optional[str] = payload.get("sub")
    user_id: Optional[int] = payload.get("id")
    if not email or not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token payload")
        
    new_access_token = create_access_token(data={"sub": email, "id": user_id})

    response.set_cookie(
        key="access_token",
        value=new_access_token,
        httponly=True,
        max_age=1800,
        expires=1800,
        path="/",
        samesite="lax",
        secure=True,
    )
    
    return {"access_token": new_access_token, "token_type": "bearer"}

# --- Google OAuth (Commented out) ---
# @auth.get("/google/login")
# async def google_login():
#     return {
#         "url": f"https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id={settings.GOOGLE_CLIENT_ID}&redirect_uri={settings.GOOGLE_REDIRECT_URI}&scope=openid%20email%20profile"
#     }

# @auth.get("/google/callback")
# async def google_callback(code: str, session: Session = Depends(get_session)):
#     # Exchange code for token
#     token_url = "https://oauth2.googleapis.com/token"
#     data = {
#         "code": code,
#         "client_id": settings.GOOGLE_CLIENT_ID,
#         "client_secret": settings.GOOGLE_CLIENT_SECRET,
#         "redirect_uri": settings.GOOGLE_REDIRECT_URI,
#         "grant_type": "authorization_code",
#     }
#     
#     async with httpx.AsyncClient() as client:
#         response = await client.post(token_url, data=data)
#         token_data = response.json()
#         
#     access_token = token_data.get("access_token")
#     if not access_token:
#         raise HTTPException(status_code=400, detail="Failed to get google token")
#         
#     # Get user info
#     user_info_url = "https://www.googleapis.com/oauth2/v3/userinfo"
#     async with httpx.AsyncClient() as client:
#         user_info_res = await client.get(user_info_url, headers={"Authorization": f"Bearer {access_token}"})
#         user_info = user_info_res.json()
#         
#     email = user_info.get("email")
#     google_id = user_info.get("sub")
#     full_name = user_info.get("name")
#     
#     # Find or create user
#     user = session.exec(select(User).where(User.email == email)).first()
#     if not user:
#         user = User(email=email, google_id=google_id, full_name=full_name)
#         session.add(user)
#     else:
#         user.google_id = google_id
#         session.add(user)
#         
#     session.commit()
#     session.refresh(user)
#     
#     # Generate our JWT
#     token = create_access_token(data={"sub": user.email, "id": user.id})
#     return {"access_token": token, "token_type": "bearer"}

# --- GitHub OAuth (Commented out) ---
# @auth.get("/github/login")
# async def github_login():
#     return {
#         "url": f"https://github.com/login/oauth/authorize?client_id={settings.GITHUB_CLIENT_ID}&redirect_uri={settings.GITHUB_REDIRECT_URI}&scope=user:email"
#     }

# @auth.get("/github/callback")
# async def github_callback(code: str, session: Session = Depends(get_session)):
#     # Exchange code for token
#     token_url = "https://github.com/login/oauth/access_token"
#     params = {
#         "client_id": settings.GITHUB_CLIENT_ID,
#         "client_secret": settings.GITHUB_CLIENT_SECRET,
#         "code": code,
#         "redirect_uri": settings.GITHUB_REDIRECT_URI
#     }
#     headers = {"Accept": "application/json"}
#     
#     async with httpx.AsyncClient() as client:
#         response = await client.post(token_url, params=params, headers=headers)
#         token_data = response.json()
#         
#     access_token = token_data.get("access_token")
#     if not access_token:
#         raise HTTPException(status_code=400, detail="Failed to get github token")
#         
#     # Get user info
#     async with httpx.AsyncClient() as client:
#         user_res = await client.get("https://api.github.com/user", headers={"Authorization": f"token {access_token}"})
#         user_info = user_res.json()
#         
#         # GitHub might not return email in /user if it's private, we might need /user/emails
#         email = user_info.get("email")
#         if not email:
#             emails_res = await client.get("https://api.github.com/user/emails", headers={"Authorization": f"token {access_token}"})
#             emails = emails_res.json()
#             email = next((e["email"] for e in emails if e["primary"]), emails[0]["email"])
#             
#     github_id = str(user_info.get("id"))
#     full_name = user_info.get("name")
#     
#     # Find or create user
#     user = session.exec(select(User).where(User.email == email)).first()
#     if not user:
#         user = User(email=email, github_id=github_id, full_name=full_name)
#         session.add(user)
#     else:
#         user.github_id = github_id
#         session.add(user)
#         
#     session.commit()
#     session.refresh(user)
#     
#     # Generate our JWT
#     token = create_access_token(data={"sub": user.email, "id": user.id})
#     return {"access_token": token, "token_type": "bearer"}
