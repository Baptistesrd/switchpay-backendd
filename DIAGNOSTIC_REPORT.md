# SwitchPay Backend — Diagnostic Report

**Date:** 2026-04-05  
**Auditor:** Claude Sonnet 4.6  
**Scope:** Full codebase audit + Phase 2 improvements

---

## 1. Executive Summary

The backend is a well-structured FastAPI application with clear separation of
concerns across routers, services, PSP modules, schemas, and the data layer.
The routing concept — weighted score from authorization rate + latency with
geographic fallback — is sound.  However, several critical issues were found
that would cause silent failures in production:

| Severity | Issue |
|----------|-------|
| **Critical** | `smart_router.py` started with `smart router · PY\nCopy` (copy-paste artifact) — SyntaxError; production ran from stale `.pyc` |
| **Critical** | Shared SQLite `cursor` object used across all threads — race conditions under concurrent load |
| **Critical** | `call_psp` used `time.sleep()` inside async endpoint — blocked the entire event loop |
| **High** | `MIN_SAMPLE_SIZE = 5` — scoring engine trusted scores from 5 transactions, far below statistical threshold |
| **High** | `verify_api_key` accepted any string starting with `test_` — not just registered temp keys |
| **High** | No idempotency TTL enforcement — records accumulated forever |
| **Medium** | `get_all_transactions()` fetched entire table then sliced in Python — O(N) memory on every routing request |
| **Medium** | Idempotency snapshot included `raw_response` (nested PSP data) making the snapshot large |
| **Low** | `psp_stripe.py` in services was dead code (never imported) |
| **Low** | `psycopg2-binary` in requirements.txt — app uses SQLite, not PostgreSQL |

---

## 2. Architecture Diagnosis

### 2.1 App Structure

```
backend/
  main.py              — FastAPI app, CORS, health endpoint
  routers/             — HTTP layer (transaction, metrics, contact, waitlist, webhook, temp_key)
  services/            — Business logic (smart_router, payment_processor, scoring_engine, mailer)
  psps/                — PSP client modules (stripe, adyen, rapyd, wise)
  schemas/             — Pydantic models
  security/            — API key validation, temp key store
  db/                  — SQLite data access
```

The layering is clean.  Routers depend on services; services depend on DB and
PSP modules.  No circular imports.

### 2.2 Multi-tenancy

Currently the `entreprise` field is set from the API key (`org: sandbox` for
all test keys).  Full multi-tenancy would require keying the scoring engine
per organisation — currently scores are global across all tenants.

---

## 3. Scoring Engine

### Before

```python
MIN_SAMPLE_SIZE = 5           # statistically meaningless
HISTORY_WINDOW  = 200

def _compute_scores(recent_txs):
    # uniform weighting — old and new transactions equally weighted
    # no timestamps used
    # latency normalisation would divide by zero when one PSP qualifies
    scores[psp] = 0.6 * success_rate + 0.4 * latency_score
    return scores

# selection: raw max score, no confidence weighting
return max(scores, key=lambda psp: scores[psp])
```

### After (`scoring_engine.py`)

```python
MIN_SAMPLE_SIZE = 30          # requires 30 effective samples before trusting score
DECAY_LAMBDA    = 0.0001      # per-second; 3h-old tx has weight ≈0.34

# Exponential decay: w_i = exp(-λ * age_in_seconds)
# Effective sample size: N_eff = (Σw)² / Σ(w²)
# Confidence: N_eff / (N_eff + MIN_SAMPLE_SIZE)  →  [0, 1)

# Confidence-adjusted selection:
# effective_score = score * confidence + 0.5 * (1 - confidence)
```

The `select_best_psp` function now prefers a slightly lower-scoring PSP with
higher confidence over a nominally-better PSP with sparse data.

---

## 4. Changes Made

| File | Change | Reason |
|------|--------|--------|
| `backend/services/scoring_engine.py` | **Created** | Extracted + improved scoring logic; added exponential decay, N_eff, confidence, confidence-adjusted selection |
| `backend/services/smart_router.py` | **Rewritten** | Removed invalid Python header; uses `scoring_engine`; uses `get_recent_transactions(limit=)` instead of full-table fetch; uppercase country code normalisation matches schema validator |
| `backend/services/payment_processor.py` | **Rewritten** | All async (`asyncio.sleep`); full-jitter backoff (`random.uniform(0, base * 2^attempt)`); per-attempt logging with PSP name + attempt + error; uses `BasePSPClient` instances |
| `backend/psps/base.py` | **Created** | `BasePSPClient` abstract class — common interface, enforces `process_payment` contract |
| `backend/psps/stripe.py` | **Rewritten** | `StripeClient(BasePSPClient)`, async, docstring |
| `backend/psps/adyen.py` | **Rewritten** | `AdyenClient(BasePSPClient)`, async, docstring |
| `backend/psps/rapyd.py` | **Rewritten** | `RapydClient(BasePSPClient)`, async, docstring |
| `backend/psps/wise.py` | **Rewritten** | `WiseClient(BasePSPClient)`, async, docstring |
| `backend/db/db_utils.py` | **Rewritten** | `threading.Lock` around all DB ops; `get_recent_transactions(limit)` added; idempotency TTL enforcement in `get_idempotency`; `cleanup_expired_idempotency()` runs on import; `datetime.now(timezone.utc)` replaces `datetime.utcnow()` (deprecated); added `idx_tx_entreprise` index |
| `backend/schemas/transaction.py` | **Updated** | Strict Pydantic v2 validation: `montant > 0`, `devise` exactly 3 chars uppercase, `pays` exactly 2 chars uppercase, `device` 1–100 chars; `TransactionResponse.latency_ms` made Optional |
| `backend/routers/transaction.py` | **Updated** | `await` on `call_psp`; stores `result["psp_used"]` (actual PSP after failover) not just `chosen_psp`; snapshot excludes `raw_response` to keep idempotency record compact; `data.model_dump()` (Pydantic v2) |
| `backend/routers/metrics.py` | **Rewritten** | Per-PSP: authorization_rate, avg_latency_ms, transaction_count, success_count, total_volume; scoring engine scores and confidence attached when data is sufficient |
| `backend/main.py` | **Updated** | `/health` returns DB status + registered PSP list; import cleanup |
| `requirements.txt` | **Updated** | Removed `psycopg2-binary` (unused); pinned to installed versions |
| `tests/__init__.py` | **Created** | Test package marker |
| `tests/test_scoring_engine.py` | **Created** | 30 unit tests: edge cases (empty, single tx, insufficient samples, missing latency, invalid timestamps, ties), multi-PSP comparison, decay behaviour, `select_best_psp` confidence logic |
| `tests/test_integration.py` | **Created** | 14 integration tests via HTTPX + ASGITransport: happy path, failover, all-PSPs-fail, auth, Pydantic validation, idempotency replay, idempotency conflict, `/health`, `/metrics` |

---

## 5. Issues Not Fixed (Known Limitations)

### 5.1 Idempotency Race Condition
Two concurrent requests carrying the same idempotency key can both pass the
`get_idempotency` check before either has saved.  Fixing this requires a
database-level advisory lock (`SELECT ... FOR UPDATE` in Postgres) or an
application-level async mutex per key.  SQLite's WAL mode does not provide
row-level locking — the `INSERT OR REPLACE` will silently overwrite if both
requests complete.  **Mitigation:** accept the race for now; it results in a
duplicate transaction (non-destructive) rather than a crash.

### 5.2 Auth is Sandbox-Only
`verify_api_key` only validates `test_*` keys against the temp key store
structure but actually accepts *any* string starting with `test_`.  Production
requires a real API key registry (database table of hashed keys per org) with
rate limiting.  This was out of scope for this audit pass.

### 5.3 Scoring Non-Stationarity
The exponential decay partially addresses non-stationarity but cannot detect
sudden regime changes (e.g. a PSP having a 2-minute outage recovers before
the decay-weighted score drops below the threshold).  A CUSUM or EWMA control
chart on the success rate would detect step-changes faster.

### 5.4 No Per-Country Scoring
Scores are global.  A PSP that performs well in the US may perform poorly in
Brazil.  Production should compute scores stratified by `(psp, country)` — but
this multiplies the data required per stratum, making the `MIN_SAMPLE_SIZE`
issue more acute.

### 5.5 SQLite Not Production-Grade
SQLite serialises all writes through a single lock.  Under concurrent load the
DB layer will become the bottleneck.  Migrate to PostgreSQL (the `psycopg2`
dependency was already present, suggesting this was the original intent) with
`asyncpg` or SQLAlchemy async.

### 5.6 PSP Clients are Simulated
All four PSP modules return random outcomes.  Real integrations require API
keys, webhook signature verification (the Stripe webhook handler currently
does no signature validation), proper error classification (retriable vs.
permanent), and idempotency keys forwarded to the PSP.

---

## 6. Suggested Next Steps

1. **Bayesian score estimation** — replace the frequentist success rate with a
   Beta posterior `Beta(α + successes, β + failures)` seeded with an
   informative prior (e.g. `Beta(80, 4)` for Stripe).  This gives better
   estimates with few observations and naturally expresses uncertainty.

2. **Thompson Sampling for PSP selection** — instead of deterministic
   max-score selection, sample from each PSP's Beta posterior and select the
   highest sample.  This provides automatic exploration/exploitation balance,
   routing some traffic to underperforming PSPs to detect improvements.

3. **Per-country scoring** — stratify `(psp, country)` pairs once transaction
   volume supports it (raise `MIN_SAMPLE_SIZE` to 50 per stratum).

4. **PostgreSQL migration** — replace the SQLite + threading.Lock pattern with
   `asyncpg` + a proper connection pool; add `FOR UPDATE SKIP LOCKED` for the
   idempotency race fix.

5. **Real API key registry** — store hashed API keys per organisation in the
   database; add per-key rate limiting (e.g. via a Redis sliding window).

6. **Webhook signature verification** — validate `Stripe-Signature` headers on
   incoming webhook events to prevent replay attacks.

7. **Observability** — add structured JSON logging with `structlog`, export
   Prometheus metrics (transaction latency histogram, PSP error rate counter),
   and instrument the scoring engine's fallback rate.
