import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from backend.db.db_utils import save_contact_message, get_all_contact_messages
from backend.routers.temp_key_router import limiter
from backend.security.auth import verify_api_key
from backend.services.mailer import send_contact_email

logger = logging.getLogger("switchpay.contact")
router = APIRouter()

class ContactRequest(BaseModel):
    email: EmailStr
    message: str

@router.post("/contact")
@limiter.limit("5/minute")
async def create_contact(request: Request, data: ContactRequest):
    try:
        save_contact_message(data.email, data.message)

        subject = "📩 New message from switchpay"
        body = (
            f"Email: {data.email}\n\n"
            f"Message:\n{data.message}"
        )

        send_contact_email(subject, body)

        return {
            "status": "ok",
            "msg": "Message received & email sent"
        }

    except Exception as e:
        logger.error("Contact form error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred. Please try again later.",
        )


@router.get("/contact")
def list_contacts(api=Depends(verify_api_key)):
    return get_all_contact_messages()
