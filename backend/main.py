# backend/main.py

import os
import logging
from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.transaction import router as transaction_router
from backend.routers.metrics import router as metrics_router
from backend.routers.contact import router as contact_router
from backend.routers.temp_key_router import router as temp_key_router


# ============================================================
# ENV CONFIG
# ============================================================

DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

# CORS_ORIGINS = "https://switchpayglobal.com,http://localhost:3000"
RAW_CORS_ORIGINS = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS: List[str] = [
    origin.strip()
    for origin in RAW_CORS_ORIGINS.split(",")
    if origin.strip()
]


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("switchpay")

# Silence uvicorn access logs in prod
if not DEBUG:
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ============================================================
# CORS CONFIG
# ============================================================

DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]

PROD_ORIGINS = [
    "https://switchpayglobal.com",
    "https://switchpay-frontendd.onrender.com",
]

if DEBUG:
    ALLOW_ORIGINS = list(dict.fromkeys(DEV_ORIGINS + PROD_ORIGINS + CORS_ORIGINS))
else:
    # En prod, on est strict
    ALLOW_ORIGINS = CORS_ORIGINS or PROD_ORIGINS


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="SwitchPay API",
    description="Smart payment routing & observability layer",
    version="2.0.0",
)


# ============================================================
# MIDDLEWARES
# ============================================================

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Request logging ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.debug(f"➡️  {request.method} {request.url.path}")
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception("🔥 Unhandled exception")
        raise exc
    logger.debug(f"⬅️  {response.status_code} {request.method} {request.url.path}")
    return response


# ============================================================
# ROUTERS
# ============================================================

app.include_router(transaction_router, tags=["transactions"])
app.include_router(metrics_router, tags=["metrics"])
app.include_router(temp_key_router, tags=["auth"])
app.include_router(contact_router, tags=["contact"])


# ============================================================
# LIFECYCLE
# ============================================================

@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Starting SwitchPay API")
    logger.info(f"DEBUG = {DEBUG}")
    logger.info(f"CORS allow_origins = {ALLOW_ORIGINS}")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Shutting down SwitchPay API")


# ============================================================
# HEALTH & META
# ============================================================

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}

@app.get("/version", tags=["system"])
def version():
    return {
        "name": "SwitchPay API",
        "version": "2.0.0",
        "debug": DEBUG,
    }
