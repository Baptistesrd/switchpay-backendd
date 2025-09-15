from fastapi import APIRouter
from backend.db.db_utils import get_all_transactions
from collections import defaultdict

router = APIRouter()

@router.get("/metrics")
def get_metrics():
    transactions = get_all_transactions()
    total = len(transactions)
    volume = sum(tx["montant"] for tx in transactions)
    
    by_psp = defaultdict(int)
    for tx in transactions:
        by_psp[tx["psp"]] += 1

    return {
        "total_transactions": total,
        "total_volume": volume,
        "transactions_by_psp": dict(by_psp)
    }
