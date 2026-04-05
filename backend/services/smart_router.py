"""
Smart router: selects the optimal PSP for each incoming transaction.

Two selection strategies are available, controlled by the
PSP_SELECTION_STRATEGY environment variable:

  "weighted_score" (default)
      Compute an exponentially-decayed composite score per PSP (60 % auth rate
      + 40 % inverse latency).  Select the PSP with the highest
      confidence-adjusted score.  Falls back to geographic heuristics when
      no PSP has sufficient history (N_eff ≥ 30).

  "thompson"
      Model each PSP's success rate as a Beta posterior updated with decayed
      observations.  Draw one sample from each posterior and route to the
      highest draw.  PSPs with insufficient history receive the uninformative
      prior Beta(1, 1), guaranteeing exploratory traffic.  Falls back to
      geographic heuristics only when the DB is unavailable.

Both strategies share the same data fetch, geographic fallback table, and
HISTORY_WINDOW constant so that switching strategies is a single env-var change.
"""

import logging
import os

from backend.db.db_utils import get_recent_transactions
from backend.services.scoring_engine import (
    HISTORY_WINDOW,
    compute_psp_scores,
    select_best_psp,
)
from backend.services.thompson_sampling import select_psp_thompson

logger = logging.getLogger("switchpay.router")

# Known PSP names — must match keys in payment_processor.PSP_CLIENTS.
# Listed here to avoid a circular import (payment_processor → smart_router is
# not needed, but smart_router → payment_processor would be circular since
# payment_processor imports PSP classes which are independent).
_KNOWN_PSPS = ["stripe", "adyen", "rapyd", "wise"]

# Geographic heuristics: country code (ISO 3166-1 alpha-2, uppercase) → PSP
_GEO_MAP: dict = {
    # Stripe: strong in major Western markets
    "US": "stripe", "CA": "stripe", "GB": "stripe",
    "FR": "stripe", "DE": "stripe", "ES": "stripe",
    "IT": "stripe", "AU": "stripe", "JP": "stripe",
    # Adyen: strong in Northern Europe and China
    "NL": "adyen", "SE": "adyen", "NO": "adyen",
    "DK": "adyen", "FI": "adyen", "CN": "adyen",
    # Wise: strong in Eastern Europe and South/Southeast Asia
    "PL": "wise", "CZ": "wise", "HU": "wise",
    "RO": "wise", "SG": "wise", "HK": "wise", "IN": "wise",
    # Rapyd: strong in Latin America and Africa
    "BR": "rapyd", "AR": "rapyd", "MX": "rapyd",
    "CO": "rapyd", "CL": "rapyd", "ZA": "rapyd",
    "KE": "rapyd", "NG": "rapyd",
}


def _geo_fallback(country: str) -> str:
    """Return the geographically preferred PSP for a country code.

    Args:
        country: ISO 3166-1 alpha-2 country code (any case).

    Returns:
        PSP name string.  Defaults to "stripe" for unknown countries.
    """
    return _GEO_MAP.get(country.upper(), "stripe")


def smart_router(transaction: dict) -> str:
    """Select the best PSP for a transaction.

    Reads PSP_SELECTION_STRATEGY from the environment at call time so that the
    strategy can be changed without restarting the process (useful for A/B
    testing or gradual rollout of Thompson Sampling).

    Args:
        transaction: Transaction dict with at least a "pays" key (country code).

    Returns:
        PSP name string (e.g. "stripe", "adyen", "rapyd", "wise").
    """
    country  = transaction.get("pays", "")
    strategy = os.getenv("PSP_SELECTION_STRATEGY", "weighted_score").lower()

    try:
        recent_txs = get_recent_transactions(limit=HISTORY_WINDOW)
    except Exception as exc:
        logger.warning("DB read failed in smart_router, using geo fallback: %s", exc)
        return _geo_fallback(country)

    if strategy == "thompson":
        return _route_thompson(recent_txs, country)
    else:
        return _route_weighted_score(recent_txs, country)


def _route_weighted_score(recent_txs: list, country: str) -> str:
    """PSP selection using confidence-adjusted composite scores.

    Excludes PSPs with N_eff < MIN_SAMPLE_SIZE and falls back to geographic
    heuristics when no PSP qualifies.
    """
    scores = compute_psp_scores(recent_txs)
    best   = select_best_psp(scores)

    if best:
        logger.debug(
            "Router (weighted_score) selected PSP=%s score=%.4f confidence=%.4f country=%s",
            best, scores[best]["score"], scores[best]["confidence"], country,
        )
        return best

    geo = _geo_fallback(country)
    logger.debug("Router (weighted_score) geo fallback PSP=%s country=%s", geo, country)
    return geo


def _route_thompson(recent_txs: list, country: str) -> str:
    """PSP selection using Thompson Sampling.

    All known PSPs participate.  Those below the N_eff threshold receive the
    uninformative prior Beta(1, 1) — maximum exploration — rather than being
    excluded.  Geographic fallback is only used if the PSP list is empty (which
    cannot happen given _KNOWN_PSPS is hard-coded, but is kept for symmetry).
    """
    chosen, meta = select_psp_thompson(recent_txs, available_psps=_KNOWN_PSPS)

    if chosen:
        params = meta["beta_params"].get(chosen, {})
        sample = meta["samples"].get(chosen, 0.0)
        logger.debug(
            "Router (thompson) selected PSP=%s sample=%.4f mean=%.4f n_eff=%.1f country=%s",
            chosen, sample, params.get("mean", 0), params.get("n_eff", 0), country,
        )
        return chosen

    # Unreachable given _KNOWN_PSPS is non-empty, but kept for safety.
    geo = _geo_fallback(country)
    logger.debug("Router (thompson) geo fallback PSP=%s country=%s", geo, country)
    return geo
