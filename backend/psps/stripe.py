"""
Stripe PSP client (simulated).

Simulated performance profile:
  - Authorization rate: ~96 %
  - Latency:           80 – 180 ms
  - Geographic strength: US, CA, GB, EU, AU, JP
"""

import asyncio
import random

from backend.psps.base import BasePSPClient

_SUCCESS_RATE = 0.96
_LAT_MIN = 0.08   # seconds
_LAT_MAX = 0.18


class StripeClient(BasePSPClient):
    """Simulated Stripe payment client.

    In production this would call stripe.PaymentIntent.create() via the
    official Stripe Python SDK.  Authorization rates and latency ranges are
    representative of observed Stripe performance in major markets.
    """

    name = "stripe"

    async def process_payment(self, data: dict) -> dict:
        """Simulate a Stripe PaymentIntent creation and confirmation.

        Args:
            data: Transaction dict (montant, devise, pays, …).

        Returns:
            {"status": "success"|"failed", "psp_tx_id": str}
        """
        await asyncio.sleep(random.uniform(_LAT_MIN, _LAT_MAX))
        status = "success" if random.random() < _SUCCESS_RATE else "failed"
        return {
            "status": status,
            "psp_tx_id": f"stripe_{random.randint(10000, 99999)}",
        }
