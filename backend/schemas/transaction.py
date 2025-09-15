from pydantic import BaseModel
from typing import Optional

class TransactionRequest(BaseModel):
    montant: float
    devise: str
    pays: str
    device: str

class TransactionResponse(BaseModel):
    id: str
    entreprise: str
    montant: float
    devise: str
    pays: str
    psp: str
    psp_tx_id: Optional[str]
    device: str
    created_at: str
    status: str
    latency_ms: float

