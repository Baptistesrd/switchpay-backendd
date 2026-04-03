from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from backend.db.db_utils import save_waitlist, get_waitlist
from backend.services.mailer import send_waitlist_email

router = APIRouter(tags=["waitlist"])


class WaitlistRequest(BaseModel):
    email: EmailStr
    company: str | None = None
    role: str | None = None


@router.post("/waitlist")
def join_waitlist(payload: WaitlistRequest):
    try:
        save_waitlist(
            email=payload.email,
            company=payload.company,
            role=payload.role,
        )

        send_waitlist_email(
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
