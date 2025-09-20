# backend/routers/transaction.py
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from backend.schemas.transaction import TransactionRequest, TransactionResponse
from backend.services import payment_processor
from backend.db.db_utils import (
    save_transaction,
    get_transaction_by_id,
    save_idempotency,
    get_idempotency,
)
from backend.security.auth import verify_api_key
from datetime import datetime
import uuid
import time
import hashlib
import json
from typing import Optional, Dict, Any

router = APIRouter()

def compute_request_hash(payload: dict) -> str:
    # normaliser JSON pour hash stable
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

@router.post("/transaction", response_model=TransactionResponse)
async def create_transaction(
    data: TransactionRequest,
    entreprise: str = Depends(verify_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    payload = data.dict()
    # compute request hash
    req_hash = compute_request_hash(payload)

    # --- Idempotency handling ---
    if idempotency_key:
        record = get_idempotency(idempotency_key)
        if record:
            # same request => return stored snapshot
            if record["request_hash"] == req_hash and record["response_snapshot"]:
                return record["response_snapshot"]
            # different payload for same idempotency key => conflict
            raise HTTPException(status_code=409, detail="Idempotency conflict: different payload for same key")

    # === create transaction object (pending) ===
    tx_id = str(uuid.uuid4())
    chosen_psp = "stripe"  # default routing placeholder, on remplace par smart_router simple call
    try:
        from backend.services.smart_router import smart_router
        chosen_psp = smart_router(payload)
    except Exception:
        pass

    transaction_data: Dict[str, Any] = {
        "id": tx_id,
        "entreprise": entreprise,
        "montant": data.montant,
        "devise": data.devise,
        "pays": data.pays,
        "psp": chosen_psp,
        "psp_tx_id": None,
        "device": data.device,
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "latency_ms": None,
        "raw_response": None,
    }

    start = time.perf_counter()
    result = payment_processor.call_psp(chosen_psp, transaction_data)
    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    transaction_data["status"] = result.get("status", "failed")
    transaction_data["psp_tx_id"] = result.get("psp_tx_id") or result.get("psp_id") or None
    transaction_data["latency_ms"] = latency_ms
    transaction_data["raw_response"] = result

    # persist transaction
    save_transaction(transaction_data)

    # persist idempotency snapshot (if key provided)
    if idempotency_key:
        # snapshot: ce qu'on renverra au client en cas de hit
        snapshot = transaction_data.copy()
        save_idempotency(idempotency_key, req_hash, tx_id, snapshot)

    # return
    return transaction_data

# --- autres routes (get/list/patch/webhook) restent inchangées, adapte si besoin ---
