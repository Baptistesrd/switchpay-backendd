import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["webhooks"])

logger = logging.getLogger("switchpay")


class WebhookStripePayload(BaseModel):
    tx_id: str
    status: str
    event_type: Optional[str] = "payment_intent.updated"


@router.post("/webhook/stripe")
def stripe_webhook(payload: WebhookStripePayload):
    logger.info(
        "Webhook received | event=%s tx_id=%s status=%s",
        payload.event_type,
        payload.tx_id,
        payload.status,
    )
    return {"received": True, "status": "simulated"}
