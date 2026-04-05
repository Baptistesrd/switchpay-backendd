"""
Pydantic models for transaction request and response validation.
"""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class TransactionRequest(BaseModel):
    """Validated inbound transaction payload.

    All fields are required.  The router uses ``pays`` for geographic fallback
    and ``devise`` / ``montant`` are forwarded to the PSP.
    """

    montant: float = Field(
        gt=0,
        description="Transaction amount in the specified currency (must be positive).",
    )
    devise: str = Field(
        min_length=3,
        max_length=3,
        description="ISO 4217 three-letter currency code (e.g. USD, EUR).",
    )
    pays: str = Field(
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code (e.g. US, FR).",
    )
    device: str = Field(
        min_length=1,
        max_length=100,
        description="Client device identifier or user agent string.",
    )

    @field_validator("devise")
    @classmethod
    def devise_uppercase(cls, v: str) -> str:
        """Normalise currency codes to uppercase."""
        return v.upper()

    @field_validator("pays")
    @classmethod
    def pays_uppercase(cls, v: str) -> str:
        """Normalise country codes to uppercase."""
        return v.upper()


class TransactionResponse(BaseModel):
    """Outbound transaction record returned after processing."""

    id: str
    entreprise: str
    montant: float
    devise: str
    pays: str
    psp: str
    psp_tx_id: Optional[str] = None
    device: str
    created_at: str
    status: str
    latency_ms: Optional[float] = None
