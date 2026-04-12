import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from backend.db.db_utils import save_waitlist, get_waitlist
from backend.routers.temp_key_router import limiter
from backend.security.auth import verify_api_key
from backend.services.mailer import send_waitlist_email

logger = logging.getLogger("switchpay.waitlist")
router = APIRouter(tags=["waitlist"])


class WaitlistRequest(BaseModel):
    email: EmailStr
    company: str | None = None
    role: str | None = None


@router.post("/waitlist")
@limiter.limit("5/minute")
def join_waitlist(request: Request, payload: WaitlistRequest):
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
        logger.error("Waitlist signup error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred. Please try again later.",
        )


@router.get("/waitlist")
def list_waitlist(api=Depends(verify_api_key)):
    return get_waitlist()
