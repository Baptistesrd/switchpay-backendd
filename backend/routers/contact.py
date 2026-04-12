from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from backend.db.db_utils import save_contact_message, get_all_contact_messages
from backend.security.auth import verify_api_key
from backend.services.mailer import send_contact_email

router = APIRouter()

class ContactRequest(BaseModel):
    email: EmailStr
    message: str

@router.post("/contact")
async def create_contact(data: ContactRequest):
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
        raise HTTPException(
            status_code=500,
            detail=f"Contact failed: {str(e)}"
        )


@router.get("/contact")
def list_contacts(api=Depends(verify_api_key)):
    return get_all_contact_messages()
