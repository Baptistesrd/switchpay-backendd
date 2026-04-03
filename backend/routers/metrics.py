from fastapi import APIRouter
from collections import defaultdict

from backend.db.db_utils import get_all_transactions

router = APIRouter(
    prefix="",
    tags=["metrics"],
)

@router.get("/metrics")
def get_metrics():
    transactions = get_all_transactions() or []

    total_transactions = len(transactions)
    total_volume = sum(float(tx.get("montant", 0)) for tx in transactions)

    transactions_by_psp = defaultdict(int)
    for tx in transactions:
        psp = tx.get("psp", "unknown")
        transactions_by_psp[psp] += 1

    return {
        "total_transactions": total_transactions,
        "total_volume": total_volume,
        "transactions_by_psp": dict(transactions_by_psp),
    }
