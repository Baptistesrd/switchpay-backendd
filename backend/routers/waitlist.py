# backend/routers/waitlist.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from backend.db.db_utils import save_waitlist, get_waitlist
from backend.utils.email import send_waitlist_notification

router = APIRouter(tags=["waitlist"])


class WaitlistRequest(BaseModel):
    email: EmailStr
    company: str | None = None
    role: str | None = None


@router.post("/waitlist")
def join_waitlist(payload: WaitlistRequest):
    try:
        # 1️⃣ Save in DB
        save_waitlist(
            email=payload.email,
            company=payload.company,
            role=payload.role,
        )

        # 2️⃣ Send email notification
        send_waitlist_notification(
            email=payload.email,
            company=payload.company,
            role=payload.role,
        )

        return {
            "status": "ok",
            "message": "You are on the waitlist 🚀",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/waitlist")
def list_waitlist():
    return get_waitlist()
