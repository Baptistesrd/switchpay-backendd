from fastapi import Header, HTTPException

def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=403, detail="API key missing")

    if x_api_key.startswith("test_"):
        return {"org": "sandbox"}

    raise HTTPException(status_code=403, detail="Invalid API key")
