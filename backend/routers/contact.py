from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from backend.db.db_utils import save_contact_message, get_all_contact_messages
from backend.services.mailer import send_email

router = APIRouter()


# ============================================================
# SCHEMA
# ============================================================

class ContactRequest(BaseModel):
    email: EmailStr
    message: str


# ============================================================
# ROUTES
# ============================================================

@router.post("/contact")
async def create_contact(data: ContactRequest):
    """
    Save contact message and send notification email.
    Email sending is awaited to ensure reliability on Render.
    """
    try:
        # 1️⃣ Save in database
        save_contact_message(data.email, data.message)

        # 2️⃣ Prepare email
        subject = "📩 Nouveau message SwitchPay"
        body = (
            f"Email: {data.email}\n\n"
            f"Message:\n{data.message}"
        )

        # 3️⃣ Send email (blocking but reliable)
        await send_email(subject, body)

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
def list_contacts():
    """
    Admin/debug endpoint to list stored contact messages.
    """
    return get_all_contact_messages()
