from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from config import settings

from pydantic import EmailStr

conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=settings.USE_CREDENTIALS,
    VALIDATE_CERTS=settings.VALIDATE_CERTS
)

async def send_otp_email(email_to: str, otp: str):
    message = MessageSchema(
        subject="Your Authentication OTP",
        recipients=[email_to],
        body=f"Your OTP code is: {otp}. It will expire in 10 minutes.",
        subtype=MessageType.html
    )

    fm = FastMail(conf)
    # If MAIL_SERVER is empty, we just print the OTP for development
    if not settings.MAIL_SERVER:
        print(f"DEBUG: Email to {email_to} with OTP {otp} (No mail server configured)")
        return
    
    await fm.send_message(message)
