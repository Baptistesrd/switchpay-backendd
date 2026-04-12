import json
import logging
import os
from typing import Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["webhooks"])

logger = logging.getLogger("switchpay")


class WebhookStripePayload(BaseModel):
    tx_id: str
    status: str
    event_type: Optional[str] = "payment_intent.updated"


@router.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=400, detail="Webhook secret not configured")

    raw_body = await request.body()
    try:
        stripe.WebhookSignature.verify_header(
            raw_body.decode("utf-8"), stripe_signature or "", secret
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    payload = WebhookStripePayload(**json.loads(raw_body))
    logger.info(
        "Webhook received | event=%s tx_id=%s status=%s",
        payload.event_type,
        payload.tx_id,
        payload.status,
    )
    return {"received": True, "status": "simulated"}
