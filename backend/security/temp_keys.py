# backend/security/temp_keys.py

import secrets
import time

# Dictionnaire en mémoire pour stocker les clés temporaires
TEMP_KEYS = {}

# Durée de validité des clés temporaires (en secondes)
TTL_SECONDS = 600  # 10 minutes

def generate_temp_key() -> str:
    """
    Génère une clé API temporaire et la stocke en mémoire
    """
    key = f"test_{secrets.token_hex(8)}"
    TEMP_KEYS[key] = {"created_at": time.time()}
    return key

def validate_temp_key(key: str) -> bool:
    """
    Vérifie si une clé temporaire est valide (existe et n’a pas expiré)
    """
    data = TEMP_KEYS.get(key)
    if not data:
        return False
    if time.time() - data["created_at"] > TTL_SECONDS:
        TEMP_KEYS.pop(key, None)
        return False
    return True
