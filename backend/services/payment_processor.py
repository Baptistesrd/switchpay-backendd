# backend/services/payment_processor.py
import os
import random
import time
import traceback
from typing import Dict, Any, List

# import tes modules simulés existants
from backend.psps import stripe as psp_stripe
from backend.psps import rapyd as psp_rapyd
from backend.psps import wise as psp_wise
from backend.psps import ayden as psp_adyen

# mapping name -> module
PSP_MODULES = {
    "stripe": psp_stripe,
    "rapyd": psp_rapyd,
    "wise": psp_wise,
    "adyen": psp_adyen,
}

# Définit l'ordre de fallback (exemple)
DEFAULT_FALLBACK_ORDER = ["stripe", "adyen", "rapyd", "wise"]

def call_single_psp(psp_name: str, data: Dict[str, Any]) -> dict:
    """
    Appelle le PSP simulé. Ici tu pourras remplacer par des vrais SDK calls.
    """
    mod = PSP_MODULES.get(psp_name)
    if not mod:
        return {"status": "failed", "error": f"PSP {psp_name} not found"}
    try:
        # chaque module expose process_payment
        return mod.process_payment(data)
    except Exception as e:
        # capture l'exception et renvoie un failed
        return {"status": "failed", "error": str(e), "trace": traceback.format_exc()}

def call_psp(psp_name: str, data: Dict[str, Any], fallback: List[str] | None = None) -> dict:
    """
    Tentative sur psp_name, puis fallback list si échec.
    Retry simple (exponential backoff) sur erreurs transitoires (simulé).
    """
    tried = []
    fallback = fallback or DEFAULT_FALLBACK_ORDER
    # build ordered list starting with desired psp
    order = [psp_name] + [p for p in fallback if p != psp_name]
    last_err = None

    for candidate in order:
        tried.append(candidate)
        # naive retry loop
        attempt = 0
        max_attempts = 2
        while attempt < max_attempts:
            attempt += 1
            resp = call_single_psp(candidate, data)
            if resp.get("status") == "success":
                resp["psp_used"] = candidate
                resp["attempts"] = attempt
                return resp
            # si échec, backoff et retry
            last_err = resp.get("error", "unknown")
            sleep = (0.1 * (2 ** (attempt - 1))) + random.uniform(0, 0.05)
            time.sleep(sleep)
        # si on arrive là, on passe au PSP suivant
    # aucun PSP n'a réussi
    return {"status": "failed", "error": last_err or "all psps failed", "tried": tried}
