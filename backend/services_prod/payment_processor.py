import os
import random
import time
import traceback
from typing import Dict, Any, List

from backend.psps import stripe as psp_stripe
from backend.psps import rapyd as psp_rapyd
from backend.psps import wise as psp_wise
from backend.psps import adyen as psp_adyen

PSP_MODULES = {
    "stripe": psp_stripe,
    "rapyd": psp_rapyd,
    "wise": psp_wise,
    "adyen": psp_adyen,
}

DEFAULT_FALLBACK_ORDER = ["stripe", "adyen", "rapyd", "wise"]

def call_single_psp(psp_name: str, data: Dict[str, Any]) -> dict:
    mod = PSP_MODULES.get(psp_name)
    if not mod:
        return {"status": "failed", "error": f"PSP {psp_name} not found"}
    try:
        return mod.process_payment(data)
    except Exception as e:
        return {"status": "failed", "error": str(e), "trace": traceback.format_exc()}

def call_psp(psp_name: str, data: Dict[str, Any], fallback: List[str] | None = None) -> dict:
    tried = []
    fallback = fallback or DEFAULT_FALLBACK_ORDER
    order = [psp_name] + [p for p in fallback if p != psp_name]
    last_err = None

    for candidate in order:
        tried.append(candidate)
        attempt = 0
        max_attempts = 2
        while attempt < max_attempts:
            attempt += 1
            resp = call_single_psp(candidate, data)
            if resp.get("status") == "success":
                resp["psp_used"] = candidate
                resp["attempts"] = attempt
                return resp
            last_err = resp.get("error", "unknown")
            sleep = (0.1 * (2 ** (attempt - 1))) + random.uniform(0, 0.05)
            time.sleep(sleep)
    return {"status": "failed", "error": last_err or "all psps failed", "tried": tried}
