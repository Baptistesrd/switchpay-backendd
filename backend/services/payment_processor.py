import os, random, time
from typing import Dict, Any
from backend.services.psp_stripe import create_and_confirm_intent

def call_psp(psp_name: str, data: Dict[str, Any]) -> dict:
    amount = data["montant"]
    currency = data["devise"]

    # 1) Stripe réel si clé présente
    if psp_name == "stripe" and os.getenv("STRIPE_SECRET_KEY"):
        try:
            return create_and_confirm_intent(amount, currency)
        except Exception as e:
            print(f"[STRIPE][ERROR] {e}")
            return {"status": "failed", "psp_tx_id": None}

    # 2) Sinon: simulation pour les autres PSP (inchangé)
    latency_sim = {
        "adyen": (150, 300),
        "wise": (200, 350),
        "rapyd": (300, 500),
        "dlocal": (350, 600),
        "stripe": (120, 250),  # fallback si pas de clé Stripe
    }
    status = "success" if random.random() > 0.05 else "failed"
    psp_tx_id = f"{psp_name}_{random.randint(100000, 999999)}"
    lo, hi = latency_sim.get(psp_name, (100, 300))
    time.sleep(random.uniform(lo/1000, hi/1000))
    return {"status": status, "psp_tx_id": psp_tx_id}


