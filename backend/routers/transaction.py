import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from backend.db.db_utils import (
    get_idempotency,
    get_transactions_by_org,
    save_idempotency,
    save_transaction,
)
from backend.schemas.transaction import TransactionRequest, TransactionResponse
from backend.security.auth import verify_api_key
from backend.services import payment_processor
from backend.services.smart_router import smart_router

logger = logging.getLogger("switchpay.router.transaction")

router = APIRouter()


def _request_hash(payload: dict) -> str:
    """Return a SHA-256 hex digest of the canonicalised request payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("/transaction", response_model=TransactionResponse)
async def create_transaction(
    data: TransactionRequest,
    api=Depends(verify_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Dict[str, Any]:
    """Process a payment transaction through the optimal PSP.

    If an ``Idempotency-Key`` header is provided the endpoint returns the cached
    response for duplicate requests without re-processing.  The key expires after
    24 hours (configurable via IDEMPOTENCY_TTL_SECONDS in db_utils.py).

    Returns:
        TransactionResponse with the final status, chosen PSP, and latency.

    Raises:
        409 if the same idempotency key is reused with a different payload.
    """
    payload = data.model_dump()
    req_hash = _request_hash(payload)

    # ── Idempotency check ────────────────────────────────────────────────────
    if idempotency_key:
        record = get_idempotency(idempotency_key)
        if record:
            if record["request_hash"] == req_hash and record["response_snapshot"]:
                logger.info("Idempotent replay | key=%s", idempotency_key)
                return record["response_snapshot"]
            raise HTTPException(
                status_code=409,
                detail="Idempotency conflict: different payload reuses the same key.",
            )

    # ── PSP selection ────────────────────────────────────────────────────────
    try:
        chosen_psp = smart_router(payload)
    except Exception as exc:
        logger.warning("smart_router failed, defaulting to stripe: %s", exc)
        chosen_psp = "stripe"

    tx_id = str(uuid.uuid4())
    transaction_data: Dict[str, Any] = {
        "id": tx_id,
        "entreprise": api.get("org", "sandbox"),
        "montant": data.montant,
        "devise": data.devise,
        "pays": data.pays,
        "psp": chosen_psp,
        "psp_tx_id": None,
        "device": data.device,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "latency_ms": None,
        "raw_response": None,
    }

    # ── PSP call ─────────────────────────────────────────────────────────────
    start = time.perf_counter()
    result = await payment_processor.call_psp(chosen_psp, transaction_data)
    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    # Use the PSP that actually processed the transaction (may differ from
    # chosen_psp if failover occurred).
    actual_psp = result.get("psp_used", chosen_psp)

    transaction_data["psp"] = actual_psp
    transaction_data["status"] = result.get("status", "failed")
    transaction_data["psp_tx_id"] = result.get("psp_tx_id") or result.get("psp_id")
    transaction_data["latency_ms"] = latency_ms
    transaction_data["raw_response"] = result

    save_transaction(transaction_data)

    if idempotency_key:
        snapshot = {k: v for k, v in transaction_data.items() if k != "raw_response"}
        save_idempotency(idempotency_key, req_hash, tx_id, snapshot)

    return transaction_data


@router.get("/transactions", response_model=List[TransactionResponse])
async def list_transactions(api=Depends(verify_api_key)) -> list:
    """Return all transactions for the authenticated organisation."""
    org = api.get("org")
    if not org:
        raise HTTPException(status_code=403, detail="Cannot determine organisation from API key")
    return get_transactions_by_org(org)
