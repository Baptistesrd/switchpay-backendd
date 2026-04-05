"""
Integration tests for the full transaction routing → PSP call → response cycle.

All PSP clients are mocked so tests are fast, deterministic, and offline.
The tests exercise:
  - successful routing and response shape
  - PSP failover when the preferred PSP fails
  - idempotency replay (same key + payload → same response)
  - idempotency conflict (same key + different payload → 409)
  - Pydantic validation on invalid request fields
  - /health endpoint structure
  - /metrics endpoint structure
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from backend.main import app


# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_PAYLOAD = {
    "montant": 42.50,
    "devise": "USD",
    "pays": "US",
    "device": "web",
}

AUTH_HEADERS = {"X-Api-Key": "test_integration"}


@pytest_asyncio.fixture
async def client():
    """Async HTTP test client bound to the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


def _mock_psp_success(psp_name: str = "stripe"):
    """Return an AsyncMock that simulates a successful PSP response."""
    return AsyncMock(
        return_value={
            "status": "success",
            "psp_tx_id": f"{psp_name}_mock_001",
            "psp_used": psp_name,
            "attempts": 1,
        }
    )


def _mock_psp_failure():
    """Return an AsyncMock that simulates a failed PSP response (all PSPs fail)."""
    return AsyncMock(
        return_value={
            "status": "failed",
            "error": "simulated failure",
            "tried": ["stripe", "adyen", "rapyd", "wise"],
        }
    )


# ── /transaction ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_transaction_returns_200(client):
    """Happy path: valid payload, PSP succeeds → 200 with correct shape."""
    with patch(
        "backend.services.payment_processor.call_psp",
        new=_mock_psp_success("stripe"),
    ):
        resp = await client.post(
            "/transaction", json=VALID_PAYLOAD, headers=AUTH_HEADERS
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["psp"] == "stripe"
    assert body["psp_tx_id"] == "stripe_mock_001"
    assert body["montant"] == 42.50
    assert body["devise"] == "USD"
    assert "id" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_failover_uses_actual_psp(client):
    """When failover occurs, the stored PSP should be the one that succeeded."""
    with patch(
        "backend.services.payment_processor.call_psp",
        new=AsyncMock(
            return_value={
                "status": "success",
                "psp_tx_id": "adyen_mock_999",
                "psp_used": "adyen",  # failover happened
                "attempts": 2,
            }
        ),
    ):
        resp = await client.post(
            "/transaction", json=VALID_PAYLOAD, headers=AUTH_HEADERS
        )

    assert resp.status_code == 200
    assert resp.json()["psp"] == "adyen"


@pytest.mark.asyncio
async def test_all_psps_fail_returns_failed_status(client):
    """If every PSP fails, the transaction is persisted with status 'failed'."""
    with patch(
        "backend.services.payment_processor.call_psp",
        new=_mock_psp_failure(),
    ):
        resp = await client.post(
            "/transaction", json=VALID_PAYLOAD, headers=AUTH_HEADERS
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_missing_api_key_returns_403(client):
    resp = await client.post("/transaction", json=VALID_PAYLOAD)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_negative_amount_rejected(client):
    payload = {**VALID_PAYLOAD, "montant": -10.0}
    resp = await client.post("/transaction", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_zero_amount_rejected(client):
    payload = {**VALID_PAYLOAD, "montant": 0}
    resp = await client.post("/transaction", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_currency_length_rejected(client):
    payload = {**VALID_PAYLOAD, "devise": "US"}  # must be 3 chars
    resp = await client.post("/transaction", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_country_length_rejected(client):
    payload = {**VALID_PAYLOAD, "pays": "USA"}  # must be 2 chars
    resp = await client.post("/transaction", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 422


# ── Idempotency ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotent_replay_returns_cached_response(client):
    """Two identical requests with the same idempotency key return the same body."""
    idem_headers = {**AUTH_HEADERS, "Idempotency-Key": "test-idem-replay-001"}

    with patch(
        "backend.services.payment_processor.call_psp",
        new=_mock_psp_success("stripe"),
    ):
        r1 = await client.post(
            "/transaction", json=VALID_PAYLOAD, headers=idem_headers
        )
        r2 = await client.post(
            "/transaction", json=VALID_PAYLOAD, headers=idem_headers
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_idempotency_conflict_returns_409(client):
    """Reusing an idempotency key with a different payload must return 409."""
    idem_headers = {**AUTH_HEADERS, "Idempotency-Key": "test-idem-conflict-001"}
    different_payload = {**VALID_PAYLOAD, "montant": 999.99}

    with patch(
        "backend.services.payment_processor.call_psp",
        new=_mock_psp_success("stripe"),
    ):
        r1 = await client.post(
            "/transaction", json=VALID_PAYLOAD, headers=idem_headers
        )
        r2 = await client.post(
            "/transaction", json=different_payload, headers=idem_headers
        )

    assert r1.status_code == 200
    assert r2.status_code == 409


# ── /health ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "psps" in body
    assert isinstance(body["psps"], list)


# ── /metrics ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_structure(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "by_psp" in body
    assert "total_transactions" in body["summary"]
    assert "total_volume" in body["summary"]
