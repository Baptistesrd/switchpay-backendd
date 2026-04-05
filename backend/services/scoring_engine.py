"""
Scoring Engine for SwitchPay's dynamic PSP router.

The composite score for each PSP is:

    score = 0.6 * weighted_success_rate + 0.4 * normalized_latency_score

Transactions are weighted by recency using exponential decay:

    w_i = exp(-λ * age_in_seconds)

where λ (DECAY_LAMBDA) controls how quickly old data loses influence.
At λ=0.0001 a transaction 3 h old has weight ≈0.34; one day old ≈0.0002.

A confidence value is also returned alongside the score, derived from the
effective sample size N_eff = (Σw_i)² / Σ(w_i²).  This lets the router
prefer a slightly lower-scoring but statistically safer PSP when data is sparse.
"""

import math
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("switchpay.scoring")

# ── Tunable constants ───────────────────────────────────────────────────────
MIN_SAMPLE_SIZE: int = 30
"""Minimum effective sample size (N_eff) before a PSP's computed score is trusted.
Below this threshold the PSP is excluded and the caller falls back to geo heuristics."""

WEIGHT_SUCCESS: float = 0.6
"""Weight of the authorization-rate component in the composite score."""

WEIGHT_LATENCY: float = 0.4
"""Weight of the (inverse) latency component in the composite score."""

HISTORY_WINDOW: int = 200
"""Maximum number of recent transactions to consider when computing scores."""

DECAY_LAMBDA: float = 0.0001
"""Exponential decay rate in seconds⁻¹.
Increase to weight recent transactions more heavily; decrease for a longer memory."""


def _age_seconds(created_at_iso: Optional[str]) -> float:
    """Return the age in seconds of a transaction given its ISO-8601 created_at string.

    Falls back to 0.0 (i.e. weight = 1.0) on any parse error so that transactions
    with missing timestamps are treated as current rather than silently dropped.
    """
    if not created_at_iso:
        return 0.0
    try:
        ts = datetime.fromisoformat(created_at_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return max(0.0, age)
    except (ValueError, TypeError):
        return 0.0


def compute_psp_scores(
    recent_txs: list,
    decay_lambda: float = DECAY_LAMBDA,
) -> dict:
    """Compute a composite score for each PSP using exponentially-decayed history.

    Each transaction is assigned weight w_i = exp(-decay_lambda * age_in_seconds).
    The weighted success rate and weighted average latency are then combined into a
    single score in [0, 1].  PSPs with insufficient effective sample size are excluded.

    Args:
        recent_txs: List of transaction dicts (most recent first) with keys:
                    psp, status, latency_ms, created_at.
        decay_lambda: Decay rate (s⁻¹).  Defaults to module-level DECAY_LAMBDA.

    Returns:
        Dict mapping psp_name -> {"score": float, "confidence": float, "n_eff": float,
        "success_rate": float, "avg_latency_ms": float}.
        Empty dict if no PSP meets the minimum sample threshold.
    """
    buckets: dict = {}

    for tx in recent_txs:
        psp = tx.get("psp")
        if not psp:
            continue

        if psp not in buckets:
            buckets[psp] = {
                "w_total": 0.0,
                "w_sq": 0.0,
                "w_success": 0.0,
                "w_lat_num": 0.0,
                "w_lat_den": 0.0,
            }

        age = _age_seconds(tx.get("created_at"))
        w = math.exp(-decay_lambda * age)

        b = buckets[psp]
        b["w_total"] += w
        b["w_sq"] += w * w

        if tx.get("status") == "success":
            b["w_success"] += w

        lat = tx.get("latency_ms")
        if lat is not None:
            try:
                b["w_lat_num"] += w * float(lat)
                b["w_lat_den"] += w
            except (TypeError, ValueError):
                pass

    # Filter PSPs by effective sample size ─────────────────────────────────
    qualified: dict = {}
    for psp, b in buckets.items():
        if b["w_sq"] == 0:
            continue
        # N_eff = (Σw)² / Σ(w²) — equivalent to the number of independent observations
        n_eff = (b["w_total"] ** 2) / b["w_sq"]
        if n_eff >= MIN_SAMPLE_SIZE:
            qualified[psp] = {**b, "n_eff": n_eff}
        else:
            logger.debug(
                "PSP %s excluded: n_eff=%.1f < threshold=%d", psp, n_eff, MIN_SAMPLE_SIZE
            )

    if not qualified:
        return {}

    # Compute per-PSP success rates and average latencies ───────────────────
    success_rates = {
        psp: d["w_success"] / d["w_total"] for psp, d in qualified.items()
    }
    avg_latencies = {
        psp: (d["w_lat_num"] / d["w_lat_den"] if d["w_lat_den"] > 0 else 300.0)
        for psp, d in qualified.items()
    }

    # Normalise latencies to [0, 1] (lower latency → higher score) ──────────
    lat_values = list(avg_latencies.values())
    lat_min = min(lat_values)
    lat_max = max(lat_values)
    # Use a minimum range of 1 ms to prevent floating-point amplification when
    # two PSPs have nearly identical average latencies — differences below 1 ms
    # are not operationally meaningful and should not affect ranking.
    lat_range = max(lat_max - lat_min, 1.0)

    scores: dict = {}
    for psp in qualified:
        success_score = success_rates[psp]
        latency_score = 1.0 - (avg_latencies[psp] - lat_min) / lat_range
        composite = WEIGHT_SUCCESS * success_score + WEIGHT_LATENCY * latency_score

        n_eff = qualified[psp]["n_eff"]
        # Confidence approaches 1.0 as n_eff → ∞; equals 0.5 at n_eff = MIN_SAMPLE_SIZE.
        confidence = n_eff / (n_eff + MIN_SAMPLE_SIZE)

        scores[psp] = {
            "score": round(composite, 4),
            "confidence": round(confidence, 4),
            "n_eff": round(n_eff, 1),
            "success_rate": round(success_rates[psp], 4),
            "avg_latency_ms": round(avg_latencies[psp], 1),
        }

    return scores


def select_best_psp(scores: dict) -> Optional[str]:
    """Select the PSP with the highest confidence-adjusted score.

    The effective score used for ranking is:

        effective = score * confidence + 0.5 * (1 - confidence)

    This means a high-confidence PSP that scores 0.80 beats a low-confidence
    PSP that scores 0.85, because the 0.85 estimate is not yet reliable.

    Args:
        scores: Output of compute_psp_scores().

    Returns:
        PSP name string, or None if scores is empty.
    """
    if not scores:
        return None

    def _effective(psp: str) -> float:
        s = scores[psp]
        return s["score"] * s["confidence"] + 0.5 * (1.0 - s["confidence"])

    return max(scores, key=_effective)
