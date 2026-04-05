"""
Wise PSP client (simulated).

Simulated performance profile:
  - Authorization rate: ~93 %
  - Latency:           300 – 800 ms
  - Geographic strength: PL, CZ, HU, RO, SG, HK, IN
"""

import asyncio
import random

from backend.psps.base import BasePSPClient

_SUCCESS_RATE = 0.93
_LAT_MIN = 0.30   # seconds
_LAT_MAX = 0.80


class WiseClient(BasePSPClient):
    """Simulated Wise (formerly TransferWise) payment client.

    In production this would call the Wise Platform API.  Wise has strong
    coverage in Eastern Europe and Southeast Asia but higher baseline latency
    due to compliance checks on cross-border transfers.
    """

    name = "wise"

    async def process_payment(self, data: dict) -> dict:
        """Simulate a Wise payment transfer.

        Args:
            data: Transaction dict (montant, devise, pays, …).

        Returns:
            {"status": "success"|"failed", "psp_tx_id": str}
        """
        await asyncio.sleep(random.uniform(_LAT_MIN, _LAT_MAX))
        status = "success" if random.random() < _SUCCESS_RATE else "failed"
        return {
            "status": status,
            "psp_tx_id": f"wise_{random.randint(10000, 99999)}",
        }
