import os
from fastapi import Header, HTTPException


def _load_api_keys() -> dict:
    raw = os.environ.get("API_KEYS", "").strip()
    if not raw:
        raise RuntimeError(
            "API_KEYS env var is missing or empty. "
            "Set it to comma-separated key:OrgName pairs (e.g. sk_live_abc:Acme)."
        )
    keys = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            raise RuntimeError(
                f"Malformed API_KEYS entry (expected key:OrgName): {entry!r}"
            )
        key, org = entry.split(":", 1)
        keys[key.strip()] = org.strip()
    return keys


_API_KEYS: dict = _load_api_keys()


def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=403, detail="API key missing")
    org = _API_KEYS.get(x_api_key)
    if org is None:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return {"org": org}
