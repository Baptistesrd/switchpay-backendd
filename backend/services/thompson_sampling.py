"""
Thompson Sampling for PSP selection in SwitchPay's routing engine.

WHY THIS EXISTS
───────────────
The weighted scoring engine in scoring_engine.py always routes to the argmax
PSP — the one with the highest point-estimate composite score.  That is greedy
and creates a concrete failure mode: if a PSP has a bad streak early in the
data window, its score stays low and it never gets traffic again, even after
it has recovered.  The system gets stuck.

Thompson Sampling fixes this by routing to the PSP with the highest *sampled*
success rate, not the highest *estimated* one.  Because sampling from a wide
posterior occasionally yields a high value, uncertain PSPs naturally receive
exploratory traffic without any explicit "explore vs. exploit" switch.


WHY BETA DISTRIBUTION
──────────────────────
Each transaction outcome is binary: success (1) or failure (0).  The natural
probability model for binary outcomes is the Bernoulli distribution with
parameter p (the true success rate).

The Beta distribution is the conjugate prior for the Bernoulli likelihood.
"Conjugate" means the prior and posterior have the same functional form, which
makes Bayesian updating trivially cheap:

    Prior  : Beta(α₀, β₀)
    Data   : s successes, f failures
    Posterior: Beta(α₀ + s, β₀ + f)   ← just add counts, no integration

Beta(α, β) has mean α/(α+β) and variance αβ / ((α+β)²(α+β+1)).  As s and f
grow, the variance shrinks and the mean converges to the true success rate.


WHY UNIFORM PRIOR  Beta(1, 1)
──────────────────────────────
Beta(1, 1) is the uniform distribution on [0, 1] — every success rate between
0% and 100% is equally plausible before we see any data.  This is the maximum-
entropy prior for a bounded probability: it encodes no initial preference for
any PSP and maximises exploration of new or un-sampled providers.

An informative prior like Beta(80, 4) ≈ 95% would bias the router toward PSPs
that historically perform like Stripe.  That may be appropriate in production
once you have prior knowledge, but it is out of scope here.


WHY EXPONENTIAL DECAY ON THE BAYESIAN UPDATE
─────────────────────────────────────────────
Standard Bayesian updates treat every observation as equally informative
(weight = 1).  We extend this to fractional weights:

    w_i = exp(-λ * age_in_seconds)

An old transaction contributes weight w_i < 1 — it is "partially observed."
Adding 0.4 weighted successes to α is equivalent to saying "we trust this
observation 40%."  The posterior still updates in the same direction, just
less aggressively for stale data.

This is statistically analogous to a Kalman filter's process-noise term: it
prevents the posterior from becoming overconfident about historical behaviour
and allows it to track non-stationary PSP performance.

λ = 1e-5 s⁻¹ is deliberately slower than scoring_engine's 1e-4 s⁻¹.
Thompson Sampling needs enough accumulated evidence per PSP to converge to
a reliable posterior.  Faster decay would require more recent data, increasing
the cold-start problem for infrequently-used PSPs.

At λ = 1e-5:
  - 1 hour  old: w ≈ 0.96   (nearly full weight)
  - 1 day   old: w ≈ 0.42   (still meaningful)
  - 1 week  old: w ≈ 0.002  (nearly forgotten)
  - 1 month old: w ≈ 8×10⁻¹³ (negligible)
"""

import math
import random as _random
from typing import Optional

from backend.services.scoring_engine import MIN_SAMPLE_SIZE, _age_seconds

# ── Module constants ─────────────────────────────────────────────────────────

DECAY_LAMBDA: float = 1e-5
"""Exponential decay rate in s⁻¹ for Thompson Sampling.
Slower than scoring_engine (1e-4) to give PSPs time to accumulate
a reliable posterior before being compared against peers."""


# ── Core functions ────────────────────────────────────────────────────────────

def compute_beta_params(
    transactions: list,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    decay_lambda: float = DECAY_LAMBDA,
) -> dict:
    """Compute the Beta posterior parameters for each PSP from transaction history.

    For each PSP p that appears in ``transactions``, the posterior is:

        alpha_p = prior_alpha + Σ(w_i  for successful transactions of p)
        beta_p  = prior_beta  + Σ(w_i  for failed transactions of p)
        w_i     = exp(-decay_lambda * age_in_seconds_i)

    Old transactions contribute fractional weight via exponential decay, so the
    posterior reflects recent performance more heavily than distant history
    without discarding old observations entirely.

    The effective sample size N_eff = (Σw)² / Σ(w²) is the Kish design-effect
    estimate: the number of unweighted observations that would give equivalent
    statistical precision.  It is stored in the output so callers can decide
    whether to trust the posterior or fall back to the prior.

    Args:
        transactions: List of transaction dicts with keys psp, status, created_at.
        prior_alpha:  α parameter of the Beta prior (default 1.0 = uniform).
        prior_beta:   β parameter of the Beta prior (default 1.0 = uniform).
        decay_lambda: Decay rate in s⁻¹.  Higher = forgets faster.

    Returns:
        Dict mapping psp_name → {alpha, beta, n_eff, mean, variance}.
        Only PSPs that appear in ``transactions`` are included.
        The returned alpha/beta already include the prior terms.
    """
    # Accumulate per-PSP weighted success/failure counts.
    buckets: dict = {}

    for tx in transactions:
        psp = tx.get("psp")
        if not psp:
            continue  # skip rows with no PSP assignment

        if psp not in buckets:
            buckets[psp] = {
                "w_success": 0.0,  # Σ w_i for successes
                "w_failure": 0.0,  # Σ w_i for failures
                "w_total":   0.0,  # Σ w_i (= w_success + w_failure)
                "w_sq":      0.0,  # Σ w_i² — needed for N_eff denominator
            }

        age = _age_seconds(tx.get("created_at"))
        # w_i = exp(-λ * age) decays monotonically from 1.0 (brand-new) to ≈0
        w = math.exp(-decay_lambda * age)

        b = buckets[psp]
        b["w_total"] += w
        # w² accumulates the denominator of the Kish N_eff formula
        b["w_sq"] += w * w

        if tx.get("status") == "success":
            b["w_success"] += w
        else:
            # Anything that is not "success" counts as a failure.
            # This is intentional: declined, errored, timed-out are all
            # evidence that the PSP did not serve the transaction.
            b["w_failure"] += w

    result: dict = {}
    for psp, b in buckets.items():
        # N_eff = (Σw)² / Σ(w²).  When all weights are equal (w_i = c),
        # N_eff = (nc)²/(nc²) = n, so it equals the raw count.
        # When weights vary, N_eff < n by the Cauchy-Schwarz inequality.
        n_eff = (b["w_total"] ** 2) / b["w_sq"] if b["w_sq"] > 0 else 0.0

        # Posterior parameters = prior + weighted observations.
        # Because Beta is the conjugate prior for Bernoulli, this is the
        # exact Bayesian update — no approximation.
        alpha = prior_alpha + b["w_success"]
        beta  = prior_beta  + b["w_failure"]

        # Beta(α, β) statistics — useful for display and diagnostics.
        total    = alpha + beta
        mean     = alpha / total
        # Variance = αβ / ((α+β)² * (α+β+1)).  Shrinks as total grows.
        variance = (alpha * beta) / (total ** 2 * (total + 1))

        result[psp] = {
            "alpha":    round(alpha, 4),
            "beta":     round(beta, 4),
            "n_eff":    round(n_eff, 1),
            "mean":     round(mean, 4),
            "variance": round(variance, 6),
        }

    return result


def thompson_sample(
    beta_params: dict,
    seed: Optional[int] = None,
) -> dict:
    """Draw one sample from each PSP's Beta posterior.

    This is the core of Thompson Sampling: instead of routing to the PSP with
    the highest *mean*, we route to the PSP with the highest *sample*.

    A PSP with a wide posterior (few observations, high variance) will
    occasionally draw a sample much higher than its mean, naturally earning
    exploratory traffic.  A well-understood PSP draws near its mean most of
    the time.  As data accumulates, posteriors narrow and exploration falls
    off automatically — no tuning required.

    Args:
        beta_params: Dict mapping psp_name → {alpha, beta, …} as returned
                     by compute_beta_params or assembled by select_psp_thompson.
        seed:        Optional integer seed for reproducible draws in tests.
                     In production leave None (non-deterministic).

    Returns:
        Dict mapping psp_name → float sample in (0, 1).
    """
    # A seeded Random instance is created per call so the same seed always
    # produces the same draw sequence, regardless of other code that uses
    # the global random module.
    rng = _random.Random(seed)

    samples = {}
    for psp, params in beta_params.items():
        # random.betavariate(a, b) samples from Beta(a, b) ∈ (0, 1).
        # Python's implementation uses Johnk's method for small α,β and
        # the algorithm of Atkinson (1979) otherwise — both correct.
        samples[psp] = rng.betavariate(params["alpha"], params["beta"])

    return samples


def select_psp_thompson(
    transactions: list,
    available_psps: list,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    decay_lambda: float = DECAY_LAMBDA,
    seed: Optional[int] = None,
) -> tuple:
    """Select a PSP using Thompson Sampling.

    Decision rule:
      1. Compute the Beta posterior for each PSP that appears in the history.
      2. For each PSP in available_psps:
           - If its N_eff ≥ MIN_SAMPLE_SIZE → use the observed posterior.
           - Otherwise             → use the pure prior Beta(prior_alpha, prior_beta).
         The pure prior represents maximum uncertainty, which means maximum
         exploration: the router will send enough traffic to every new or
         recovering PSP to build up a reliable posterior.
      3. Draw one sample from each posterior.
      4. Return the PSP with the highest sample.

    The greedy scoring engine would never recover once a PSP falls behind.
    This function will: as old failures decay and/or new successes accumulate,
    the PSP's posterior shifts, its samples occasionally beat the incumbent,
    and it regains traffic.

    Args:
        transactions:  Recent transaction history (output of get_recent_transactions).
        available_psps: All PSP names the router may choose from.
        prior_alpha:   α of the Beta prior (default 1.0).
        prior_beta:    β of the Beta prior (default 1.0).
        decay_lambda:  Decay rate in s⁻¹.
        seed:          Optional seed for reproducible draws.

    Returns:
        (chosen_psp, metadata) where metadata contains:
          beta_params: final per-PSP {alpha, beta, n_eff, mean, variance}
          samples:     the raw draw values that determined the winner
    """
    # Step 1: posteriors from history (only PSPs that appear in transactions).
    from_history = compute_beta_params(
        transactions, prior_alpha, prior_beta, decay_lambda
    )

    # Step 2: build final params for every available PSP.
    all_params: dict = {}
    prior_total = prior_alpha + prior_beta
    prior_mean  = prior_alpha / prior_total
    # Variance of Beta(prior_alpha, prior_beta)
    prior_var   = (prior_alpha * prior_beta) / (prior_total ** 2 * (prior_total + 1))

    for psp in available_psps:
        observed = from_history.get(psp)
        if observed is not None and observed["n_eff"] >= MIN_SAMPLE_SIZE:
            # Enough evidence: trust the posterior.
            all_params[psp] = observed
        else:
            # Too little data: fall back to the uninformative prior.
            # Using prior_alpha/prior_beta (not the partially-updated alpha/beta)
            # gives true maximum uncertainty — the PSP is treated as if we had
            # never seen it, ensuring it gets exploratory traffic.
            all_params[psp] = {
                "alpha":    prior_alpha,
                "beta":     prior_beta,
                "n_eff":    observed["n_eff"] if observed is not None else 0.0,
                "mean":     round(prior_mean, 4),
                "variance": round(prior_var, 6),
            }

    # Step 3: sample.
    samples = thompson_sample(all_params, seed=seed)

    # Step 4: the PSP with the highest sample wins this routing decision.
    chosen = max(samples, key=lambda p: samples[p])

    return chosen, {"beta_params": all_params, "samples": samples}
