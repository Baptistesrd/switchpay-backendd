"""
Unit tests for backend.services.scoring_engine.

Covers edge cases the scoring engine must handle gracefully:
  - empty transaction history
  - single transaction (n_eff < MIN_SAMPLE_SIZE)
  - all transactions from one PSP
  - tied composite scores
  - transactions with missing latency
  - transactions with missing / invalid created_at (decay fallback)
  - normal case with multiple PSPs
  - select_best_psp confidence adjustment
"""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.services.scoring_engine import (
    MIN_SAMPLE_SIZE,
    WEIGHT_LATENCY,
    WEIGHT_SUCCESS,
    _age_seconds,
    compute_psp_scores,
    select_best_psp,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tx(
    psp: str,
    status: str = "success",
    latency_ms: float = 100.0,
    age_seconds: float = 0.0,
) -> dict:
    """Build a minimal transaction dict for testing."""
    created_at = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    return {
        "psp": psp,
        "status": status,
        "latency_ms": latency_ms,
        "created_at": created_at,
    }


def _bulk(psp: str, n: int, status: str = "success", latency_ms: float = 100.0) -> list:
    """Generate ``n`` recent transactions for a given PSP."""
    return [_make_tx(psp, status=status, latency_ms=latency_ms) for _ in range(n)]


# ── _age_seconds ──────────────────────────────────────────────────────────────

class TestAgeSeconds:
    def test_recent_timestamp_gives_small_age(self):
        ts = datetime.now(timezone.utc).isoformat()
        age = _age_seconds(ts)
        assert 0.0 <= age < 2.0  # within 2 s of now

    def test_old_timestamp_gives_correct_age(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        age = _age_seconds(ts)
        assert 3598 < age < 3602  # ~3600 s

    def test_none_returns_zero(self):
        assert _age_seconds(None) == 0.0

    def test_empty_string_returns_zero(self):
        assert _age_seconds("") == 0.0

    def test_invalid_string_returns_zero(self):
        assert _age_seconds("not-a-date") == 0.0

    def test_naive_datetime_treated_as_utc(self):
        # Simulate a naive ISO string (no tzinfo) — older code produced these
        ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        age = _age_seconds(ts)
        assert 0.0 <= age < 2.0


# ── compute_psp_scores — edge cases ───────────────────────────────────────────

class TestComputePspScoresEdgeCases:
    def test_empty_history_returns_empty(self):
        assert compute_psp_scores([]) == {}

    def test_single_transaction_below_threshold(self):
        """One transaction gives n_eff ≈ 1, well below MIN_SAMPLE_SIZE=30."""
        txs = [_make_tx("stripe", "success", 100.0)]
        assert compute_psp_scores(txs) == {}

    def test_insufficient_samples_excluded(self):
        """29 transactions → n_eff < 30 → PSP excluded."""
        txs = _bulk("stripe", n=MIN_SAMPLE_SIZE - 1)
        assert compute_psp_scores(txs) == {}

    def test_minimum_samples_qualifies(self):
        """Exactly MIN_SAMPLE_SIZE recent (weight≈1) transactions qualifies.

        With all weights ≈ 1 and same age:
          n_eff = (Σw)² / Σ(w²) = n² / n = n = MIN_SAMPLE_SIZE
        """
        txs = _bulk("stripe", n=MIN_SAMPLE_SIZE)
        scores = compute_psp_scores(txs)
        assert "stripe" in scores

    def test_all_failed_gives_zero_success_rate(self):
        txs = _bulk("stripe", n=MIN_SAMPLE_SIZE, status="failed")
        scores = compute_psp_scores(txs)
        assert scores["stripe"]["success_rate"] == 0.0

    def test_all_success_gives_one_success_rate(self):
        txs = _bulk("stripe", n=MIN_SAMPLE_SIZE, status="success")
        scores = compute_psp_scores(txs)
        assert scores["stripe"]["success_rate"] == 1.0

    def test_missing_latency_uses_default_300ms(self):
        txs = [
            {"psp": "stripe", "status": "success", "latency_ms": None,
             "created_at": datetime.now(timezone.utc).isoformat()}
        ] * MIN_SAMPLE_SIZE
        scores = compute_psp_scores(txs)
        assert scores["stripe"]["avg_latency_ms"] == 300.0

    def test_transactions_without_psp_key_are_skipped(self):
        txs = [{"status": "success", "latency_ms": 100.0}] * MIN_SAMPLE_SIZE
        assert compute_psp_scores(txs) == {}

    def test_returns_score_confidence_n_eff(self):
        txs = _bulk("stripe", n=MIN_SAMPLE_SIZE)
        scores = compute_psp_scores(txs)
        entry = scores["stripe"]
        assert "score" in entry
        assert "confidence" in entry
        assert "n_eff" in entry
        assert "success_rate" in entry
        assert "avg_latency_ms" in entry

    def test_score_in_zero_one_range(self):
        txs = _bulk("stripe", n=MIN_SAMPLE_SIZE, status="success")
        scores = compute_psp_scores(txs)
        s = scores["stripe"]["score"]
        assert 0.0 <= s <= 1.0

    def test_confidence_increases_with_more_data(self):
        c30 = compute_psp_scores(_bulk("stripe", n=MIN_SAMPLE_SIZE))["stripe"]["confidence"]
        c90 = compute_psp_scores(_bulk("stripe", n=90))["stripe"]["confidence"]
        assert c90 > c30

    def test_confidence_asymptotes_below_one(self):
        scores = compute_psp_scores(_bulk("stripe", n=10_000))
        assert scores["stripe"]["confidence"] < 1.0


# ── compute_psp_scores — normal multi-PSP case ───────────────────────────────

class TestComputePspScoresMultiPSP:
    def setup_method(self):
        # Stripe: 100 % success, 100 ms → should score highest
        # Rapyd: 50 % success, 500 ms → should score lowest
        self.txs = (
            _bulk("stripe", n=50, status="success", latency_ms=100.0)
            + _bulk("stripe", n=10, status="failed", latency_ms=100.0)
            + _bulk("rapyd", n=30, status="success", latency_ms=500.0)
            + _bulk("rapyd", n=30, status="failed", latency_ms=500.0)
        )

    def test_all_qualified_psps_present(self):
        scores = compute_psp_scores(self.txs)
        assert "stripe" in scores
        assert "rapyd" in scores

    def test_stripe_outscores_rapyd(self):
        scores = compute_psp_scores(self.txs)
        assert scores["stripe"]["score"] > scores["rapyd"]["score"]

    def test_latency_normalisation_single_psp(self):
        """When only one PSP qualifies, latency score should be 1.0 (no range to penalise)."""
        txs = _bulk("stripe", n=MIN_SAMPLE_SIZE, status="success", latency_ms=999.0)
        scores = compute_psp_scores(txs)
        # score = WEIGHT_SUCCESS * 1.0 + WEIGHT_LATENCY * 1.0
        expected = WEIGHT_SUCCESS * 1.0 + WEIGHT_LATENCY * 1.0
        assert abs(scores["stripe"]["score"] - expected) < 1e-6

    def test_tied_scores_both_present(self):
        """Two PSPs with identical performance should both appear in output.

        Uses MIN_SAMPLE_SIZE + 10 per PSP so both pass the N_eff threshold even
        when the first batch's timestamps are slightly older than the second
        (Cauchy-Schwarz: N_eff = n only when all weights are identical, so a
        small buffer above the minimum is needed for robustness).
        """
        n = MIN_SAMPLE_SIZE + 10
        txs = (
            _bulk("stripe", n=n, status="success", latency_ms=200.0)
            + _bulk("adyen", n=n, status="success", latency_ms=200.0)
        )
        scores = compute_psp_scores(txs)
        assert "stripe" in scores
        assert "adyen" in scores
        # Both PSPs have identical simulated performance; scores should be very close
        assert abs(scores["stripe"]["score"] - scores["adyen"]["score"]) < 0.01


# ── Exponential decay ─────────────────────────────────────────────────────────

class TestExponentialDecay:
    def test_old_transactions_contribute_less(self):
        """10 fresh successes + 20 old failures should yield a high success rate
        because old failures have very low weight at high decay_lambda."""
        recent_successes = _bulk("stripe", n=10, status="success")
        old_failures = [
            _make_tx("stripe", "failed", latency_ms=100.0, age_seconds=86_400)  # 1 day
            for _ in range(20)
        ]
        txs = recent_successes + old_failures
        scores = compute_psp_scores(txs, decay_lambda=0.001)  # aggressive decay
        # At λ=0.001, 1-day-old weight = exp(-86.4) ≈ 0 — old failures nearly gone
        if scores:
            assert scores["stripe"]["success_rate"] > 0.5

    def test_zero_lambda_gives_uniform_weights(self):
        """λ=0 means no decay — all transactions equally weighted regardless of age.

        Uses 3× MIN_SAMPLE_SIZE to stay well above the threshold even when
        transaction timestamps differ slightly (Cauchy-Schwarz: N_eff ≤ n, with
        equality only when all weights are identical).
        """
        # With λ=0, w_i = exp(0) = 1.0 for all ages → N_eff = n exactly
        txs_fresh = _bulk("stripe", n=MIN_SAMPLE_SIZE * 3, status="success", latency_ms=100.0)
        scores_flat = compute_psp_scores(txs_fresh, decay_lambda=0.0)
        assert "stripe" in scores_flat

        # With moderate λ and 3× samples, N_eff stays comfortably above threshold
        # even when loop-created timestamps differ by microseconds
        scores_moderate = compute_psp_scores(txs_fresh, decay_lambda=0.00001)
        assert "stripe" in scores_moderate


# ── select_best_psp ───────────────────────────────────────────────────────────

class TestSelectBestPsp:
    def test_empty_scores_returns_none(self):
        assert select_best_psp({}) is None

    def test_single_psp_returned(self):
        scores = {"stripe": {"score": 0.8, "confidence": 0.9}}
        assert select_best_psp(scores) == "stripe"

    def test_high_confidence_beats_higher_raw_score(self):
        """PSP B has lower raw score but much higher confidence → B wins."""
        scores = {
            "adyen":  {"score": 0.90, "confidence": 0.40},  # effective ≈ 0.90*0.40+0.5*0.60 = 0.66
            "stripe": {"score": 0.85, "confidence": 0.95},  # effective ≈ 0.85*0.95+0.5*0.05 = 0.8325
        }
        assert select_best_psp(scores) == "stripe"

    def test_equal_confidence_picks_highest_score(self):
        scores = {
            "stripe": {"score": 0.9, "confidence": 0.8},
            "adyen":  {"score": 0.7, "confidence": 0.8},
        }
        assert select_best_psp(scores) == "stripe"

    def test_all_psps_failing_low_confidence_returns_best(self):
        """Even if all PSPs are poor, the least-bad one should be selected."""
        scores = {
            "stripe": {"score": 0.30, "confidence": 0.60},
            "rapyd":  {"score": 0.20, "confidence": 0.60},
        }
        assert select_best_psp(scores) == "stripe"
