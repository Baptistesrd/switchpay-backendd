import random
import time

def process_payment(data: dict) -> dict:
    time.sleep(random.uniform(0.05, 0.15))
    return {
        "status": "success",
        "psp_tx_id": f"adyen_{random.randint(10000, 99999)}"
    }


