import random
import time

_SUCCESS_RATE = 0.82   
_LAT_MIN      = 0.20   
_LAT_MAX      = 0.60   

def process_payment(data: dict) -> dict:
    time.sleep(random.uniform(_LAT_MIN, _LAT_MAX))
    status = "success" if random.random() < _SUCCESS_RATE else "failed"
    return {
        "status": status,
        "psp_tx_id": f"rapyd_{random.randint(10000, 99999)}"
    }
