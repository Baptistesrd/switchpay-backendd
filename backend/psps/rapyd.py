"""
Rapyd PSP client (simulated).

Simulated performance profile:
  - Authorization rate: ~82 %
  - Latency:           200 – 600 ms
  - Geographic strength: LatAm (BR, AR, MX, CO, CL), Africa (ZA, KE, NG)
"""

import asyncio
import random

from backend.psps.base import BasePSPClient

_SUCCESS_RATE = 0.82
_LAT_MIN = 0.20   # seconds
_LAT_MAX = 0.60


class RapydClient(BasePSPClient):
    """Simulated Rapyd payment client.

    In production this would call the Rapyd Collect API.  Rapyd's lower
    authorization rate reflects its role as an emerging-market specialist —
    the router should prefer it only for countries where it outperforms alternatives.
    """

    name = "rapyd"

    async def process_payment(self, data: dict) -> dict:
        """Simulate a Rapyd payment submission.

        Args:
            data: Transaction dict (montant, devise, pays, …).

        Returns:
            {"status": "success"|"failed", "psp_tx_id": str}
        """
        await asyncio.sleep(random.uniform(_LAT_MIN, _LAT_MAX))
        status = "success" if random.random() < _SUCCESS_RATE else "failed"
        return {
            "status": status,
            "psp_tx_id": f"rapyd_{random.randint(10000, 99999)}",
        }
