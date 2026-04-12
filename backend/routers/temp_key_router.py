from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.security.auth import verify_api_key
from backend.security.temp_keys import generate_temp_key

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()


@router.get("/generate-temp-key")
@limiter.limit("5/minute")
def generate_temp_api_key(request: Request, api=Depends(verify_api_key)):
    """
    Génère une API key temporaire pour les tests.
    Elle expire automatiquement après 10 min ou lors du redémarrage du serveur.
    """
    key = generate_temp_key()
    return {"api_key": key, "expires_in_seconds": 600}
