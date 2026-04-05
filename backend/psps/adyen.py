"""
Adyen PSP client (simulated).

Simulated performance profile:
  - Authorization rate: ~97 %
  - Latency:           150 – 350 ms
  - Geographic strength: NL, SE, NO, DK, FI, CN
"""

import asyncio
import random

from backend.psps.base import BasePSPClient

_SUCCESS_RATE = 0.97
_LAT_MIN = 0.15   # seconds
_LAT_MAX = 0.35


class AdyenClient(BasePSPClient):
    """Simulated Adyen payment client.

    In production this would call the Adyen Checkout API (POST /payments).
    Adyen's authorization rate is marginally higher than Stripe's in the
    simulation but at the cost of higher latency.
    """

    name = "adyen"

    async def process_payment(self, data: dict) -> dict:
        """Simulate an Adyen payment submission.

        Args:
            data: Transaction dict (montant, devise, pays, …).

        Returns:
            {"status": "success"|"failed", "psp_tx_id": str}
        """
        await asyncio.sleep(random.uniform(_LAT_MIN, _LAT_MAX))
        status = "success" if random.random() < _SUCCESS_RATE else "failed"
        return {
            "status": status,
            "psp_tx_id": f"adyen_{random.randint(10000, 99999)}",
        }
