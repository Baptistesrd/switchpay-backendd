import secrets
import time

TEMP_KEYS = {}

TTL_SECONDS = 600  

def generate_temp_key() -> str:
    key = f"test_{secrets.token_hex(8)}"
    TEMP_KEYS[key] = {"created_at": time.time()}
    return key

def validate_temp_key(key: str) -> bool:
    data = TEMP_KEYS.get(key)
    if not data:
        return False
    if time.time() - data["created_at"] > TTL_SECONDS:
        TEMP_KEYS.pop(key, None)
        return False
    return True
