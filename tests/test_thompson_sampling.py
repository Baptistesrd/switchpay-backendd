"""
Unit tests for backend.services.thompson_sampling.

Each test docstring explains *why* the expected behaviour follows from the
statistical model, not just *what* is being checked.  A reader who has not
seen Thompson Sampling before should be able to reconstruct the algorithm
from these tests alone.

Core invariants tested:
  1. Empty history          → all PSPs fall back to the prior Beta(1, 1)
  2. Known counts           → alpha/beta arithmetic verified exactly
  3. Exponential decay      → a recent success outweighs an old one
  4. Reproducibility        → same seed ⇒ same draw sequence
  5. Dominance              → Beta(100, 1) beats Beta(1, 1) in >90 % of draws
  6. Strategy env-var       → PSP_SELECTION_STRATEGY=thompson routes via TS
  7. Edge cases             → single PSP, all-same outcome, missing fields
"""

import math
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.services.scoring_engine import MIN_SAMPLE_SIZE
from backend.services.thompson_sampling import (
    DECAY_LAMBDA,
    compute_beta_params,
    select_psp_thompson,
    thompson_sample,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tx(psp: str, status: str = "success", age_seconds: float = 0.0) -> dict:
    """Minimal transaction dict for testing."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {"psp": psp, "status": status, "created_at": ts}


def _bulk(psp: str, n: int, status: str = "success", age_seconds: float = 0.0) -> list:
    return [_tx(psp, status=status, age_seconds=age_seconds) for _ in range(n)]


# ── compute_beta_params ───────────────────────────────────────────────────────

class TestComputeBetaParams:

    def test_empty_history_returns_empty_dict(self):
        """With no transaction history, compute_beta_params has nothing to process
        and returns an empty dict.  The prior is only applied in select_psp_thompson
        when building the params for available PSPs — compute_beta_params itself
        is purely descriptive of what the data says.
        """
        assert compute_beta_params([]) == {}

    def test_prior_is_added_to_observed_counts(self):
        """The Beta posterior is Prior + Data.

        With default prior Beta(1, 1) and 3 successes, 2 failures:
          alpha = 1 + 3 = 4
          beta  = 1 + 2 = 3

        This is the exact conjugate update: no approximation, no rounding
        except in the final rounding for display (4 decimal places).

        All weights are approximately 1.0 because the transactions are fresh
        (age ≈ 0 → exp(-λ * 0) = 1.0), so weighted counts ≈ raw counts.
        """
        txs = _bulk("stripe", 3, "success") + _bulk("stripe", 2, "failed")
        params = compute_beta_params(txs, prior_alpha=1.0, prior_beta=1.0)

        assert "stripe" in params
        # alpha = prior + weighted_successes ≈ 1.0 + 3.0 = 4.0
        assert abs(params["stripe"]["alpha"] - 4.0) < 0.01
        # beta  = prior + weighted_failures  ≈ 1.0 + 2.0 = 3.0
        assert abs(params["stripe"]["beta"] - 3.0) < 0.01

    def test_mean_equals_alpha_over_alpha_plus_beta(self):
        """The mean of Beta(α, β) is α/(α+β).

        This is the expected success rate under the posterior, equivalent to
        a Laplace-smoothed estimate: (prior_alpha + successes) / (prior_alpha +
        prior_beta + total_observations).  With uniform prior (1,1) and 3/5
        successes: mean = 4/7 ≈ 0.571.
        """
        txs = _bulk("stripe", 3, "success") + _bulk("stripe", 2, "failed")
        params = compute_beta_params(txs)
        expected_mean = params["stripe"]["alpha"] / (
            params["stripe"]["alpha"] + params["stripe"]["beta"]
        )
        assert abs(params["stripe"]["mean"] - expected_mean) < 1e-4

    def test_variance_shrinks_with_more_data(self):
        """Variance = αβ / ((α+β)²(α+β+1)).

        As we accumulate more observations, α and β grow proportionally and
        the denominator grows faster than the numerator.  Variance → 0 as
        n → ∞.  This is the mathematical expression of "we become more
        certain about the true success rate with more data."
        """
        few  = compute_beta_params(_bulk("stripe", 5, "success"))
        many = compute_beta_params(_bulk("stripe", 100, "success"))
        assert many["stripe"]["variance"] < few["stripe"]["variance"]

    def test_all_successes_pushes_mean_toward_one(self):
        """100 successes, 0 failures → alpha = 101, beta = 1.
        mean = 101/102 ≈ 0.99.
        The posterior strongly concentrates near 1.0 but never reaches it
        exactly because the prior Beta(1,1) contributed β=1 to the denominator.
        """
        params = compute_beta_params(_bulk("stripe", 100, "success"))
        assert params["stripe"]["mean"] > 0.98

    def test_all_failures_pushes_mean_toward_zero(self):
        """100 failures, 0 successes → alpha = 1, beta = 101.
        mean = 1/102 ≈ 0.0098.
        The posterior strongly concentrates near 0.0 but never reaches it.
        """
        params = compute_beta_params(_bulk("stripe", 100, "failed"))
        assert params["stripe"]["mean"] < 0.02

    def test_n_eff_kish_formula(self):
        """N_eff = (Σw)² / Σ(w²) — the Kish design-effect estimator.

        When all weights are equal (all transactions fresh, age ≈ 0):
          N_eff = (nw)² / (nw²) = n²w² / nw² = n

        So N_eff equals the raw count when data is uniformly weighted.
        We add a buffer of 2 to account for the slight weight variation
        introduced by loop iteration time (transactions created microseconds
        apart have marginally different ages).
        """
        n = 50
        params = compute_beta_params(_bulk("stripe", n, "success"))
        # N_eff ≤ n by Cauchy-Schwarz; with fresh data it is close to n.
        assert params["stripe"]["n_eff"] > n - 2
        assert params["stripe"]["n_eff"] <= n

    def test_transactions_without_psp_are_skipped(self):
        """Rows with no 'psp' key are silently skipped.
        They carry no information about which provider was used.
        """
        txs = [{"status": "success", "created_at": datetime.now(timezone.utc).isoformat()}] * 10
        assert compute_beta_params(txs) == {}

    def test_multiple_psps_computed_independently(self):
        """Each PSP's posterior is independent of other PSPs' data.

        Stripe's alpha/beta should reflect only Stripe's transactions,
        unaffected by however many Rapyd transactions are in the window.
        """
        txs = _bulk("stripe", 10, "success") + _bulk("rapyd", 10, "failed")
        params = compute_beta_params(txs)
        assert params["stripe"]["mean"] > 0.8   # mostly successes
        assert params["rapyd"]["mean"]  < 0.2   # mostly failures


# ── Exponential decay ─────────────────────────────────────────────────────────

class TestExponentialDecay:

    def test_recent_success_contributes_more_than_old_success(self):
        """A success from 1 second ago has weight exp(-λ * 1) ≈ 1.0.
        A success from 1 day ago has weight exp(-λ * 86400) ≈ 0.42 at λ=1e-5.

        Therefore, a PSP with 1 recent success and 0 recent failures should
        have a higher alpha than a PSP with 1 day-old success and 0 failures,
        because the recent weight is closer to 1.0.
        """
        fresh = compute_beta_params([_tx("stripe", "success", age_seconds=1)])
        old   = compute_beta_params([_tx("stripe", "success", age_seconds=86_400)])

        # Both have 1 success, so alpha = prior + weight.
        # Fresh weight ≈ 1.0, old weight ≈ 0.42 at DECAY_LAMBDA=1e-5.
        assert fresh["stripe"]["alpha"] > old["stripe"]["alpha"]

    def test_old_failures_barely_affect_posterior(self):
        """At λ=1e-5, a 2-week-old failure has weight exp(-1e-5 * 1_209_600) ≈ 6e-6.

        Adding 50 such failures to a PSP that has 10 recent successes (weight≈1)
        should barely move the posterior mean, because the total weighted failure
        mass is only 50 × 6e-6 ≈ 0.0003.  The prior Beta(1,1) already contributes
        β=1 to the beta parameter, so the failures are negligible in comparison.
        """
        two_weeks = 14 * 24 * 3600  # seconds
        txs = (
            _bulk("stripe", 10, "success", age_seconds=0)
            + _bulk("stripe", 50, "failed", age_seconds=two_weeks)
        )
        params = compute_beta_params(txs, decay_lambda=1e-5)
        # 10 recent successes dominate; old failures add almost nothing.
        assert params["stripe"]["mean"] > 0.85

    def test_decay_lambda_zero_treats_all_ages_equally(self):
        """λ=0 means no decay: w_i = exp(0) = 1.0 for every transaction.

        In this case the Bayesian update is the standard (unweighted) one:
          alpha = prior_alpha + count_of_successes
          beta  = prior_beta  + count_of_failures
        regardless of how old each transaction is.
        """
        txs = (
            _bulk("stripe", 5, "success", age_seconds=0)
            + _bulk("stripe", 3, "failed",  age_seconds=86_400)  # 1 day old
        )
        params_no_decay = compute_beta_params(txs, decay_lambda=0.0)

        # alpha ≈ 1 + 5 = 6, beta ≈ 1 + 3 = 4 (all weights = 1)
        assert abs(params_no_decay["stripe"]["alpha"] - 6.0) < 0.01
        assert abs(params_no_decay["stripe"]["beta"]  - 4.0) < 0.01

    def test_custom_prior_shifts_posterior(self):
        """The prior represents knowledge before seeing any data.

        With an informative prior Beta(10, 1) (prior belief: ~90% success rate)
        and 5 observed failures, the posterior is Beta(10, 6):
          alpha = 10 + 0 = 10
          beta  = 1  + 5 = 6
          mean  = 10/16 = 0.625

        The posterior is pulled *toward* the likelihood (failures) but tempered
        by the strong prior — it does not drop as far as a uniform prior would.
        """
        txs = _bulk("stripe", 5, "failed")
        params = compute_beta_params(txs, prior_alpha=10.0, prior_beta=1.0)

        # alpha ≈ 10 (prior) + 0 (no successes) = 10
        assert abs(params["stripe"]["alpha"] - 10.0) < 0.1
        # beta  ≈ 1 (prior) + 5 (failures)    = 6
        assert abs(params["stripe"]["beta"] - 6.0) < 0.1
        assert abs(params["stripe"]["mean"] - 10.0 / 16.0) < 0.01


# ── thompson_sample ───────────────────────────────────────────────────────────

class TestThompsonSample:

    def test_reproducibility_with_seed(self):
        """The same integer seed must produce bit-for-bit identical draws.

        This is essential for:
          - Debugging: reproduce a routing decision exactly.
          - A/B testing: control experiments need deterministic routing.
          - Unit tests: avoid flaky tests that depend on luck.

        We create a fresh Random(seed) instance per call, so the seed controls
        the full draw regardless of what the global random state is.
        """
        params = {
            "stripe": {"alpha": 10.0, "beta": 2.0},
            "rapyd":  {"alpha": 2.0,  "beta": 10.0},
        }
        s1 = thompson_sample(params, seed=42)
        s2 = thompson_sample(params, seed=42)
        assert s1 == s2

    def test_different_seeds_give_different_draws(self):
        """Two different seeds should (with overwhelming probability) yield
        different draws.  The probability that Beta samples are identical up to
        Python's float precision is negligibly small for any reasonable α, β.
        """
        params = {"stripe": {"alpha": 5.0, "beta": 5.0}}
        s1 = thompson_sample(params, seed=1)
        s2 = thompson_sample(params, seed=2)
        # Not guaranteed, but the probability of collision is ~10⁻¹⁶.
        assert s1["stripe"] != s2["stripe"]

    def test_samples_are_in_zero_one_interval(self):
        """Beta(α, β) is defined on (0, 1) — all samples must lie in this range.

        In Python, random.betavariate can return values extremely close to 0 or 1
        but never exactly 0 or 1 due to floating-point representation.
        """
        params = {
            "stripe": {"alpha": 1.0,   "beta": 1.0},   # uniform
            "adyen":  {"alpha": 100.0, "beta": 1.0},   # concentrated near 1
            "rapyd":  {"alpha": 1.0,   "beta": 100.0}, # concentrated near 0
        }
        for seed in range(20):
            samples = thompson_sample(params, seed=seed)
            for psp, val in samples.items():
                assert 0.0 < val < 1.0, f"{psp} sample {val} outside (0,1)"

    def test_dominant_psp_wins_majority_of_draws(self):
        """Beta(100, 1) has mean ≈ 0.99 and very low variance.
        Beta(1, 1) is the uniform distribution with mean 0.5 and high variance.

        Over 1000 independent draws, Beta(100, 1) should win more than 90 % of
        the time.  This is the mathematical guarantee that Thompson Sampling
        converges: once a PSP has accumulated strong evidence of high performance,
        it wins the vast majority of routing decisions.

        The 90 % threshold is conservative.  The true win rate for Beta(100, 1)
        vs Beta(1, 1) is approximately 99.5 % because the sample from Beta(100, 1)
        almost never falls below 0.9, while Beta(1, 1) rarely samples above 0.9.
        """
        params = {
            "good": {"alpha": 100.0, "beta": 1.0},
            "new":  {"alpha": 1.0,   "beta": 1.0},
        }
        wins = sum(
            1
            for seed in range(1000)
            if thompson_sample(params, seed=seed)["good"]
               > thompson_sample(params, seed=seed)["new"]
        )
        assert wins > 900, f"Expected >900 wins, got {wins}"

    def test_uncertain_psp_occasionally_beats_strong_psp(self):
        """Beta(1, 1) (high uncertainty) should beat Beta(10, 1) in at least
        1 % of 1000 draws.

        This is the *exploration* property of Thompson Sampling: a new or
        recovering PSP with little data occasionally samples a high value and
        receives traffic, allowing the system to detect if it has improved.

        Without this property (e.g. with pure argmax), a PSP that falls behind
        would never receive traffic again.
        """
        params = {
            "established": {"alpha": 10.0, "beta": 1.0},
            "uncertain":   {"alpha": 1.0,  "beta": 1.0},
        }
        uncertain_wins = sum(
            1
            for seed in range(1000)
            if thompson_sample(params, seed=seed)["uncertain"]
               > thompson_sample(params, seed=seed)["established"]
        )
        assert uncertain_wins >= 10, (
            f"Expected at least 10 exploratory wins, got {uncertain_wins}. "
            "A PSP with no data should occasionally be routed to."
        )

    def test_empty_params_returns_empty_dict(self):
        """No PSPs → no samples.  The caller is responsible for the fallback."""
        assert thompson_sample({}) == {}


# ── select_psp_thompson ───────────────────────────────────────────────────────

class TestSelectPspThompson:

    def test_unknown_psps_receive_prior_not_zero_data(self):
        """PSPs in available_psps that do not appear in the transaction history
        should receive the prior Beta(1, 1), not be excluded.

        This is the key difference from the scoring engine's hard exclusion:
        Thompson Sampling treats unknown PSPs as maximally uncertain and routes
        some exploratory traffic to them.  Without this, a new PSP would never
        receive its first transaction.
        """
        # No history at all.
        chosen, meta = select_psp_thompson(
            transactions=[],
            available_psps=["stripe", "adyen"],
            seed=0,
        )
        assert chosen in ("stripe", "adyen")

        # Both PSPs should have the uninformative prior: alpha=1, beta=1.
        for psp in ("stripe", "adyen"):
            assert meta["beta_params"][psp]["alpha"] == 1.0
            assert meta["beta_params"][psp]["beta"]  == 1.0

    def test_below_threshold_psp_falls_back_to_prior(self):
        """A PSP with N_eff < MIN_SAMPLE_SIZE (30) has insufficient data for
        a reliable posterior.  Rather than using a noisy posterior, select_psp_thompson
        resets it to the prior Beta(1, 1).

        This ensures PSPs with sparse data receive full exploratory traffic
        instead of being penalised by a noisy (and potentially misleading) estimate.
        The scoring engine would exclude such a PSP entirely; Thompson instead
        gives it maximum uncertainty, which is the honest representation.
        """
        # 5 transactions — well below MIN_SAMPLE_SIZE=30.
        txs = _bulk("stripe", 5, "success") + _bulk("adyen", 5, "failed")
        _, meta = select_psp_thompson(txs, available_psps=["stripe", "adyen"], seed=0)

        # Both PSPs are below threshold → both fall back to prior.
        assert meta["beta_params"]["stripe"]["alpha"] == 1.0
        assert meta["beta_params"]["stripe"]["beta"]  == 1.0
        assert meta["beta_params"]["adyen"]["alpha"]  == 1.0
        assert meta["beta_params"]["adyen"]["beta"]   == 1.0

    def test_above_threshold_psp_uses_posterior(self):
        """A PSP with N_eff ≥ MIN_SAMPLE_SIZE (30) should use its observed
        posterior, not the prior.

        With 40 successes and 0 failures:
          alpha ≈ 1 + 40 = 41  (not 1.0 from prior alone)
          beta  ≈ 1 + 0  = 1
        """
        n = MIN_SAMPLE_SIZE + 10  # 40, safely above threshold
        txs = _bulk("stripe", n, "success")
        _, meta = select_psp_thompson(txs, available_psps=["stripe"], seed=0)

        # alpha must reflect the 40 observed successes.
        assert meta["beta_params"]["stripe"]["alpha"] > 10.0

    def test_result_contains_beta_params_and_samples(self):
        """select_psp_thompson must return (chosen_psp, metadata) where metadata
        has 'beta_params' and 'samples' keys.  The caller uses these for logging
        and for the /metrics endpoint's 'thompson' section.
        """
        chosen, meta = select_psp_thompson([], ["stripe"], seed=0)
        assert isinstance(chosen, str)
        assert "beta_params" in meta
        assert "samples" in meta

    def test_single_available_psp_always_chosen(self):
        """When only one PSP is available, it is always chosen regardless of
        its posterior (there is no competitor to compare against).
        """
        for seed in range(10):
            chosen, _ = select_psp_thompson([], ["stripe"], seed=seed)
            assert chosen == "stripe"

    def test_strong_psp_wins_majority_end_to_end(self):
        """Integration check: after accumulating strong evidence for one PSP,
        it should win the routing decision in the large majority of draws.

        Uses 90 draws to build a strong posterior, then checks 100 routing
        decisions.  The strong PSP (90 successes) should win far more often
        than the weak one (90 failures).
        """
        n = MIN_SAMPLE_SIZE + 60  # well above threshold
        txs = _bulk("stripe", n, "success") + _bulk("adyen", n, "failed")
        wins = sum(
            1
            for seed in range(100)
            if select_psp_thompson(txs, ["stripe", "adyen"], seed=seed)[0] == "stripe"
        )
        assert wins >= 80, f"Expected stripe to win ≥80/100 draws, got {wins}"


# ── Strategy switching in smart_router ───────────────────────────────────────

class TestStrategyEnvVar:

    def test_default_strategy_uses_weighted_score(self):
        """When PSP_SELECTION_STRATEGY is unset, the router falls through to
        the weighted scoring engine.  With an empty DB it falls back to geo.

        We verify by mocking the DB to return empty history so neither strategy
        can compute a score, and confirming the router returns a valid PSP name
        (the geo fallback 'stripe' for country 'US').
        """
        from backend.services.smart_router import smart_router

        with patch("backend.services.smart_router.get_recent_transactions", return_value=[]):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PSP_SELECTION_STRATEGY", None)
                result = smart_router({"pays": "US"})

        assert result in ("stripe", "adyen", "rapyd", "wise")
        assert result == "stripe"  # geo fallback for US

    def test_thompson_strategy_is_activated_by_env_var(self):
        """Setting PSP_SELECTION_STRATEGY=thompson must route through Thompson
        Sampling instead of the scoring engine.

        We verify by patching select_psp_thompson and confirming it is called
        exactly once when the strategy is set to 'thompson'.
        """
        from backend.services.smart_router import smart_router

        with patch("backend.services.smart_router.get_recent_transactions", return_value=[]):
            with patch(
                "backend.services.smart_router.select_psp_thompson",
                return_value=("adyen", {"beta_params": {}, "samples": {}}),
            ) as mock_ts:
                with patch.dict(os.environ, {"PSP_SELECTION_STRATEGY": "thompson"}):
                    result = smart_router({"pays": "US"})

        mock_ts.assert_called_once()
        assert result == "adyen"

    def test_unknown_strategy_falls_back_to_weighted_score(self):
        """An unrecognised strategy name defaults to the weighted scoring engine.
        This prevents a misconfigured env var from crashing the router.
        """
        from backend.services.smart_router import smart_router

        with patch("backend.services.smart_router.get_recent_transactions", return_value=[]):
            with patch.dict(os.environ, {"PSP_SELECTION_STRATEGY": "not_a_real_strategy"}):
                result = smart_router({"pays": "US"})

        assert result in ("stripe", "adyen", "rapyd", "wise")

    def test_strategy_is_read_per_call_not_at_import(self):
        """PSP_SELECTION_STRATEGY is read inside smart_router() at call time,
        not at module import time.  This means the strategy can be changed at
        runtime (e.g. feature flags, A/B tests) without a process restart.

        We verify by calling smart_router twice with different env vars in the
        same process and confirming it takes different code paths each time.
        """
        from backend.services.smart_router import smart_router

        call_log = []

        def fake_weighted(txs, country):
            call_log.append("weighted")
            return "stripe"

        def fake_thompson(txs, country):
            call_log.append("thompson")
            return "adyen"

        with patch("backend.services.smart_router.get_recent_transactions", return_value=[]):
            with patch("backend.services.smart_router._route_weighted_score", side_effect=fake_weighted):
                with patch("backend.services.smart_router._route_thompson", side_effect=fake_thompson):
                    with patch.dict(os.environ, {"PSP_SELECTION_STRATEGY": "weighted_score"}):
                        smart_router({"pays": "US"})
                    with patch.dict(os.environ, {"PSP_SELECTION_STRATEGY": "thompson"}):
                        smart_router({"pays": "US"})

        assert call_log == ["weighted", "thompson"]
