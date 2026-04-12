"""
Metrics router — aggregated performance data for dashboard and monitoring.
"""

import logging

from fastapi import APIRouter

from backend.db.db_utils import get_psp_metrics, get_recent_transactions
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
    base = get_psp_metrics()
    by_psp = base["by_psp"]

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
        "summary": base["summary"],
        "by_psp": by_psp,
        "thompson": thompson,
    }
