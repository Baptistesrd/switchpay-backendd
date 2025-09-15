import os
from fastapi import Header, HTTPException
from dotenv import load_dotenv

load_dotenv()

API_KEYS = {}
env_keys = os.getenv("API_KEYS")

if env_keys:
    for pair in env_keys.split(","):
        key, entreprise = pair.split(":")
        API_KEYS[key.strip()] = entreprise.strip()

print(f"[DEBUG] API_KEYS chargées depuis .env : {API_KEYS}")

def verify_api_key(x_api_key: str = Header(...)) -> str:
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return API_KEYS[x_api_key]

