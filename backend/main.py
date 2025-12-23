# backend/main.py

import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.transaction import router as transaction_router
from backend.routers.metrics import router as metrics_router
from backend.routers.contact import router as contact_router
from backend.routers.temp_key_router import router as temp_key_router
from backend.routers.waitlist import router as waitlist_router

# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="switchpay API",
    version="2.0.0",
)

# ============================================================
# CORS — FIX FINAL
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://switchpayglobal.com",
        "https://switchpay-frontendd.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("switchpay")

@app.on_event("startup")
async def startup():
    logger.info("🚀 SwitchPay API started")

# ============================================================
# ROUTERS
# ============================================================

app.include_router(transaction_router)
app.include_router(metrics_router)
app.include_router(temp_key_router)
app.include_router(contact_router)
app.include_router(waitlist_router)

# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    return {"status": "ok"}
