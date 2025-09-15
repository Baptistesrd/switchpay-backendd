# backend/routers/transaction.py
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from backend.schemas.transaction import TransactionRequest, TransactionResponse
from backend.services.smart_router import smart_router
from backend.db.db_utils import (
    save_transaction,
    get_transaction_by_id,
    conn,
    cursor,
    get_idempotency_record,
    create_idempotency_placeholder,
    complete_idempotency_record,
)
from backend.services.payment_processor import call_psp
from backend.security.auth import verify_api_key
from datetime import datetime
import uuid
import time
import json
import hashlib
from typing import Optional, List, Dict, Any

router = APIRouter()


def fingerprint_request(data: TransactionRequest) -> str:
    """
    Empreinte stable de la charge utile pour vérifier la cohérence d'une même clé.
    """
    payload = json.dumps(data.dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@router.post("/transaction", response_model=TransactionResponse)
async def create_transaction(
    data: TransactionRequest,
    entreprise: str = Depends(verify_api_key),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    # ===== Idempotency: lecture/placeholder =====
    req_fp = fingerprint_request(data)

    if idempotency_key:
        existing = get_idempotency_record(idempotency_key)
        if existing:
            # déjà finalisée -> renvoyer exactement la même réponse
            if existing.get("state") == "completed":
                if existing.get("request_fingerprint") != req_fp:
                    # même clé avec payload différent → on refuse
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key already used with a different payload.",
                    )
                return existing["response_body"]

            # en cours
            raise HTTPException(
                status_code=409,
                detail="A request with this Idempotency-Key is already in progress. Retry later.",
            )
        else:
            create_idempotency_placeholder(
                key=idempotency_key,
                entreprise=entreprise,
                endpoint="/transaction",
                method="POST",
                request_fingerprint=req_fp,
            )

    # ===== Traitement normal =====
    tx_id = str(uuid.uuid4())
    psp = smart_router(data.dict())

    transaction_data: Dict[str, Any] = {
        "id": tx_id,
        "entreprise": entreprise,
        "montant": data.montant,
        "devise": data.devise,
        "pays": data.pays,
        "psp": psp,
        "psp_tx_id": None,
        "device": data.device,
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending",
        "latency_ms": None,
    }

    start = time.perf_counter()
    result = call_psp(psp, transaction_data)
    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    transaction_data["status"] = result.get("status", "failed")
    transaction_data["psp_tx_id"] = result.get("psp_tx_id")
    transaction_data["latency_ms"] = latency_ms

    # log + persist
    print(f"[METRICS] tx_id={tx_id} psp={psp} latency_ms={latency_ms}")
    save_transaction(transaction_data)

    response_payload = {**transaction_data}

    # ===== Idempotency: enregistre la réponse finale =====
    if idempotency_key:
        try:
            complete_idempotency_record(
                key=idempotency_key,
                response_body=response_payload,
                status_code=200,
                tx_id=tx_id,
            )
        except Exception as e:
            # on ne bloque pas la réponse
            print(f"[WARN] Failed to complete idempotency record: {e}")

    return response_payload


@router.get("/transaction/{tx_id}")
async def get_transaction(tx_id: str):
    transaction = get_transaction_by_id(tx_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.get("/transactions")
async def list_transactions(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Pagination simple pour éviter de renvoyer des milliers de lignes.
    """
    cursor.execute(
        "SELECT * FROM transactions ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = cursor.fetchall()
    return [dict(zip([c[0] for c in cursor.description], row)) for row in rows]


@router.patch("/transaction/{tx_id}/status")
async def update_transaction_status(tx_id: str, status: str):
    tx = get_transaction_by_id(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    cursor.execute("UPDATE transactions SET status = ? WHERE id = ?", (status, tx_id))
    conn.commit()
    return {"message": f"Transaction {tx_id} updated to status '{status}'"}


@router.post("/webhook/{psp}")
async def webhook_update_status(psp: str, data: dict):
    """
    Met à jour le statut d'une transaction en fonction du webhook du PSP.
    """
    tx_id = data.get("tx_id")
    status = data.get("status")

    if not tx_id or not status:
        raise HTTPException(status_code=400, detail="Invalid data")

    # Récupérer la transaction
    transaction = get_transaction_by_id(tx_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Mettre à jour le statut
    cursor.execute("UPDATE transactions SET status = ? WHERE id = ?", (status, tx_id))
    conn.commit()

    return {"message": f"Transaction {tx_id} status updated to '{status}'"}
