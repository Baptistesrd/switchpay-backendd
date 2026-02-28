import random
import time

_SUCCESS_RATE = 0.96  
_LAT_MIN      = 0.08   
_LAT_MAX      = 0.18   

def process_payment(data: dict) -> dict:
    time.sleep(random.uniform(_LAT_MIN, _LAT_MAX))
    status = "success" if random.random() < _SUCCESS_RATE else "failed"
    return {
        "status": status,
        "psp_tx_id": f"stripe_{random.randint(10000, 99999)}"
    }
