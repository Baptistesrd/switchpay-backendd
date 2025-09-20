# backend/main.py
import os
import logging
from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.transaction import router as transaction_router
from backend.routers.metrics import router as metrics_router

# --- Config depuis l'environnement ---
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

# CORS_ORIGINS peut être une virgule-separated list (ex: "https://app.mydomain.com,http://localhost:3000")
_raw_origins = os.getenv("CORS_ORIGINS", "").strip()
if _raw_origins:
    CORS_ORIGINS: List[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]
else:
    CORS_ORIGINS = []

# --- Détermine les origins autorisées ---
# En dev, autorise explicitement localhost (évite le wildcard '*' en prod)
if DEBUG:
    # Ajoute les variantes localhost utiles pour dev local
    dev_local = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # Préserve l'ordre : dev locales d'abord, puis ce que l'on a configuré via CORS_ORIGINS
    ALLOW_ORIGINS = list(dict.fromkeys(dev_local + CORS_ORIGINS))  # remove duplicates preserving order
    if not ALLOW_ORIGINS:
        # Fallback minimal si rien n'est fourni : autoriser tout (utile uniquement si tu veux)
        ALLOW_ORIGINS = ["*"]
else:
    # En prod, n'autorise que ce qui est explicitement listé dans CORS_ORIGINS
    ALLOW_ORIGINS = CORS_ORIGINS or []

# --- Logging basique (structuré enough for dev) ---
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("switchpay")
# Reduce noise from uvicorn access logs in non-debug mode
if not DEBUG:
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# --- FastAPI app ---
app = FastAPI(
    title="SwitchPay API",
    description="Smart router de paiements multi-PSP pour PME & marketplaces",
    version="2.0.0",
)

# --- Middleware: CORS ---
# If ALLOW_ORIGINS contains "*" FastAPI will allow everything; prefer explicit lists in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Middleware: simple request logging (pour debug) ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.debug(f"Incoming request: {request.method} {request.url.path}")
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled exception while processing request")
        raise
    logger.debug(f"Response status: {response.status_code} for {request.method} {request.url.path}")
    return response

# --- Routers métier ---
# NOTE: on ne met pas de prefix "/api" pour rester compatible avec ton frontend actuel.
app.include_router(transaction_router)
app.include_router(metrics_router)

# --- Events ---
@app.on_event("startup")
async def on_startup():
    logger.info("Starting SwitchPay API")
    logger.info(f"DEBUG={DEBUG}")
    if not ALLOW_ORIGINS:
        logger.info("CORS: no origins configured (requests from browsers will be blocked).")
    else:
        logger.info(f"CORS: allow_origins = {ALLOW_ORIGINS}")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down SwitchPay API")

# --- Health & Version endpoints ---
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"name": "SwitchPay API", "version": "2.0.0", "debug": DEBUG}
