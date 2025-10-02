from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from backend.db.db_utils import save_contact_message, get_all_contact_messages
from backend.services.mailer import send_email
import asyncio

router = APIRouter()

class ContactRequest(BaseModel):
    email: EmailStr
    message: str

@router.post("/contact")
async def create_contact(data: ContactRequest):
    print("🚀 Contact endpoint appelé (nouvelle version)")
    try:
        # Sauvegarde en DB
        save_contact_message(data.email, data.message)

        # Envoi email (async)
        subject = "📩 Nouvelle inscription/contact SwitchPay"
        body = f"Email: {data.email}\nMessage: {data.message}"
        asyncio.create_task(send_email(subject, body))

        return {"status": "ok", "msg": "Message saved & email sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/contact")
def list_contacts():
    return get_all_contact_messages()
