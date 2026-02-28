from fastapi import APIRouter
from backend.security.temp_keys import generate_temp_key

router = APIRouter()

@router.get("/generate-temp-key")
def generate_temp_api_key():
    """
    Génère une API key temporaire pour les tests.
    Elle expire automatiquement après 10 min ou lors du redémarrage du serveur.
    """
    key = generate_temp_key()
    return {"api_key": key, "expires_in_seconds": 600}
