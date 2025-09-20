# backend/main.py

import os
import logging
from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.transaction import router as transaction_router
from backend.routers.metrics import router as metrics_router

# === ENV CONFIG ===
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

# CORS_ORIGINS doit être une liste séparée par virgules
# Exemple : "https://switchpay-frontendd.onrender.com,http://localhost:3000"
_raw_origins = os.getenv("CORS_ORIGINS", "").strip()
CORS_ORIGINS: List[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# === LOGGING CONFIG ===
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("switchpay")
if not DEBUG:
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# === CORS POLICY ===
dev_origins = ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"]
prod_origins = ["https://switchpay-frontendd.onrender.com"]

if DEBUG:
    ALLOW_ORIGINS = list(dict.fromkeys(dev_origins + CORS_ORIGINS + prod_origins))
else:
    ALLOW_ORIGINS = list(dict.fromkeys(CORS_ORIGINS or prod_origins))

# === FASTAPI APP ===
app = FastAPI(
    title="SwitchPay API",
    description="Smart router de paiements multi-PSP pour PME & marketplaces",
    version="2.0.0",
)

# === MIDDLEWARE: CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === MIDDLEWARE: Logging simple des requêtes ===
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.debug(f"Incoming request: {request.method} {request.url.path}")
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled exception")
        raise
    logger.debug(f"Response status: {response.status_code} for {request.method} {request.url.path}")
    return response

# === ROUTES ===
app.include_router(transaction_router)
app.include_router(metrics_router)

# === STARTUP / SHUTDOWN ===
@app.on_event("startup")
async def on_startup():
    logger.info("Starting SwitchPay API")
    logger.info(f"DEBUG={DEBUG}")
    logger.info(f"CORS: allow_origins = {ALLOW_ORIGINS}")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down SwitchPay API")

# === HEALTH CHECK ===
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"name": "SwitchPay API", "version": "2.0.0", "debug": DEBUG}
