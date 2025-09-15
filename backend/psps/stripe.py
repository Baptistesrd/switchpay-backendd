import random
import time

def process_payment(data: dict) -> dict:
    # Simule un délai
    time.sleep(random.uniform(0.05, 0.15))
    return {
        "status": "success",  # sera écrasé par smart_router
        "psp_tx_id": f"stripe_{random.randint(10000, 99999)}"
    }


