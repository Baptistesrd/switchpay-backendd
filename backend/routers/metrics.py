"""
Metrics router — aggregated performance data for dashboard and monitoring.
"""

import logging
from collections import defaultdict

from fastapi import APIRouter

from backend.db.db_utils import get_all_transactions, get_recent_transactions
from backend.services.scoring_engine import HISTORY_WINDOW, compute_psp_scores
from backend.services.thompson_sampling import compute_beta_params

logger = logging.getLogger("switchpay.metrics")

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def get_metrics() -> dict:
    """Return aggregated per-PSP metrics for the full transaction history.

    Each PSP entry includes:
      - transaction_count: total number of transactions routed to this PSP
      - success_count:     number that resulted in "success"
      - authorization_rate: success_count / transaction_count
      - avg_latency_ms:    mean latency across all transactions for this PSP
      - total_volume:      sum of transaction amounts (in their respective currencies)

    Additionally, the response includes scoring engine data (confidence-adjusted
    scores) computed over the most recent HISTORY_WINDOW transactions.

    Returns:
        JSON object with "summary" and "by_psp" keys.
    """
    transactions = get_all_transactions() or []

    total_count = len(transactions)
    total_volume = sum(float(tx.get("montant") or 0) for tx in transactions)

    # Per-PSP aggregation
    psp_stats: dict = defaultdict(
        lambda: {
            "transaction_count": 0,
            "success_count": 0,
            "latency_sum": 0.0,
            "latency_count": 0,
            "volume": 0.0,
        }
    )

    for tx in transactions:
        psp = tx.get("psp") or "unknown"
        s = psp_stats[psp]
        s["transaction_count"] += 1
        if tx.get("status") == "success":
            s["success_count"] += 1
        lat = tx.get("latency_ms")
        if lat is not None:
            try:
                s["latency_sum"] += float(lat)
                s["latency_count"] += 1
            except (TypeError, ValueError):
                pass
        s["volume"] += float(tx.get("montant") or 0)

    by_psp = {}
    for psp, s in psp_stats.items():
        n = s["transaction_count"]
        auth_rate = s["success_count"] / n if n else 0.0
        avg_lat = s["latency_sum"] / s["latency_count"] if s["latency_count"] else None
        by_psp[psp] = {
            "transaction_count": n,
            "success_count": s["success_count"],
            "authorization_rate": round(auth_rate, 4),
            "avg_latency_ms": round(avg_lat, 1) if avg_lat is not None else None,
            "total_volume": round(s["volume"], 2),
        }

    # Fetch recent window once and share between both engine views.
    recent = []
    try:
        recent = get_recent_transactions(limit=HISTORY_WINDOW)
    except Exception as exc:
        logger.warning("Could not fetch recent transactions for engine metrics: %s", exc)

    # Weighted scoring engine view (confidence-adjusted composite score).
    try:
        scores = compute_psp_scores(recent)
        for psp, score_data in scores.items():
            if psp in by_psp:
                by_psp[psp]["score"]      = score_data["score"]
                by_psp[psp]["confidence"] = score_data["confidence"]
                by_psp[psp]["n_eff"]      = score_data["n_eff"]
    except Exception as exc:
        logger.warning("Could not attach scoring engine data to metrics: %s", exc)

    # Thompson Sampling view — per-PSP Beta posterior parameters.
    # alpha and beta are the posterior shape parameters (prior + weighted data).
    # mean = alpha / (alpha + beta) is the expected success rate.
    # variance reflects uncertainty: high when n_eff is small, shrinks with data.
    thompson: dict = {}
    try:
        thompson = compute_beta_params(recent)
    except Exception as exc:
        logger.warning("Could not compute Thompson parameters for metrics: %s", exc)

    return {
        "summary": {
            "total_transactions": total_count,
            "total_volume": round(total_volume, 2),
        },
        "by_psp": by_psp,
        "thompson": thompson,
    }
