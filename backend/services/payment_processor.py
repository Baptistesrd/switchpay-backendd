"""
PSP dispatcher with cascading failover and per-attempt exponential backoff.

Retry policy per PSP candidate:
  - max_attempts = 2  (1 initial call + 1 retry)
  - sleep = base * 2^(attempt-1) + jitter,  jitter ∈ [0, 1) s
  - base = 0.1 s

Failover order: chosen PSP first, then remaining PSPs in DEFAULT_FALLBACK_ORDER.
All PSPs are tried before the call is declared failed.
"""

import asyncio
import logging
import random
import traceback
from typing import Dict, Any, List, Optional

from backend.psps.stripe import StripeClient
from backend.psps.rapyd import RapydClient
from backend.psps.wise import WiseClient
from backend.psps.adyen import AdyenClient

logger = logging.getLogger("switchpay.processor")

PSP_CLIENTS: Dict[str, Any] = {
    "stripe": StripeClient(),
    "rapyd": RapydClient(),
    "wise": WiseClient(),
    "adyen": AdyenClient(),
}

DEFAULT_FALLBACK_ORDER: List[str] = ["stripe", "adyen", "rapyd", "wise"]
_MAX_ATTEMPTS_PER_PSP: int = 2
_BACKOFF_BASE: float = 0.1  # seconds


async def _call_single_psp(psp_name: str, data: Dict[str, Any]) -> dict:
    """Call one PSP client and return its response dict.

    Never raises; network/logic errors are captured and returned as
    {"status": "failed", "error": "…"} so the caller can decide whether to retry.

    Args:
        psp_name: Key in PSP_CLIENTS.
        data: Transaction payload forwarded to the PSP.

    Returns:
        PSP response dict with at least a "status" key.
    """
    client = PSP_CLIENTS.get(psp_name)
    if not client:
        return {"status": "failed", "error": f"unknown PSP: {psp_name}"}
    try:
        return await client.process_payment(data)
    except Exception as exc:
        logger.error("PSP %s raised unexpectedly: %s", psp_name, exc, exc_info=True)
        return {"status": "failed", "error": str(exc), "trace": traceback.format_exc()}


async def call_psp(
    psp_name: str,
    data: Dict[str, Any],
    fallback: Optional[List[str]] = None,
) -> dict:
    """Dispatch a payment through the preferred PSP with cascading failover.

    Tries ``psp_name`` first, then each PSP in ``fallback`` order (excluding
    duplicates).  Each candidate gets up to _MAX_ATTEMPTS_PER_PSP tries with
    exponential backoff + full jitter to avoid thundering herd on failures.

    Logs each attempt with PSP name, attempt number, and failure reason.

    Args:
        psp_name: The router's preferred PSP.
        data: Transaction payload.
        fallback: Ordered list of fallback PSPs.  Defaults to DEFAULT_FALLBACK_ORDER.

    Returns:
        Successful PSP response enriched with "psp_used" and "attempts" keys,
        or {"status": "failed", "error": "…", "tried": [...]} if all PSPs fail.
    """
    fallback = fallback or DEFAULT_FALLBACK_ORDER
    order = [psp_name] + [p for p in fallback if p != psp_name]
    tried: List[str] = []
    last_err: Optional[str] = None

    for candidate in order:
        tried.append(candidate)
        for attempt in range(1, _MAX_ATTEMPTS_PER_PSP + 1):
            resp = await _call_single_psp(candidate, data)

            if resp.get("status") == "success":
                logger.info(
                    "PSP call succeeded | psp=%s attempt=%d", candidate, attempt
                )
                resp["psp_used"] = candidate
                resp["attempts"] = attempt
                return resp

            last_err = resp.get("error", "unknown error")
            logger.warning(
                "PSP call failed | psp=%s attempt=%d/%d error=%s",
                candidate,
                attempt,
                _MAX_ATTEMPTS_PER_PSP,
                last_err,
            )

            if attempt < _MAX_ATTEMPTS_PER_PSP:
                # Full-jitter exponential backoff: sleep ∈ [0, base * 2^attempt)
                sleep = random.uniform(0, _BACKOFF_BASE * (2 ** attempt))
                await asyncio.sleep(sleep)

    logger.error("All PSPs failed | tried=%s last_error=%s", tried, last_err)
    return {
        "status": "failed",
        "error": last_err or "all PSPs failed",
        "tried": tried,
    }
