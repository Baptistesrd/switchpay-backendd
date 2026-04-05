"""
Abstract base class for all PSP integrations.

Every PSP client must implement process_payment() and expose a class-level
``name`` attribute that matches the key used in PSP_MODULES.
"""

from abc import ABC, abstractmethod


class BasePSPClient(ABC):
    """Common interface for Payment Service Provider clients.

    Subclasses simulate (or implement) a real PSP call.  The contract is:
    - Return a dict with at least a ``status`` key ("success" | "failed").
    - Include ``psp_tx_id`` on success.
    - Raise on unrecoverable errors; the caller handles retries.
    """

    name: str  # must be set on each subclass

    @abstractmethod
    async def process_payment(self, data: dict) -> dict:
        """Submit a payment to the PSP.

        Args:
            data: Transaction dict containing at least montant, devise, pays.

        Returns:
            Dict with keys:
                status    (str): "success" or "failed"
                psp_tx_id (str): Provider-assigned transaction ID (on success)
                error     (str, optional): Human-readable failure reason
        """
