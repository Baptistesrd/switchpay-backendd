from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.routers.contact import router as contact_router
from backend.routers.metrics import router as metrics_router
from backend.routers.temp_key_router import limiter, router as temp_key_router
from backend.routers.transaction import router as transaction_router
from backend.routers.waitlist import router as waitlist_router
from backend.routers.webhook import router as webhook_router
from backend.routers.auth import router as auth_router
from backend.db.db_utils import cleanup_expired_idempotency, ping_db
from backend.services.payment_processor import PSP_CLIENTS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("switchpay")


async def _idempotency_cleanup_loop() -> None:
    """Run cleanup_expired_idempotency every 60 minutes."""
    while True:
        cleanup_expired_idempotency()
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(application: "FastAPI"):  # type: ignore[name-defined]
    logger.info("SwitchPay API starting up")
    task = asyncio.create_task(_idempotency_cleanup_loop())
    yield
    task.cancel()
    logger.info("SwitchPay API shut down")


app = FastAPI(
    title="SwitchPay API",
    version="2.1.0",
    description="Dynamic payment routing — selects the optimal PSP per transaction.",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "https://switchpayglobal.com,https://switchpay-frontendd.onrender.com",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transaction_router)
app.include_router(metrics_router)
app.include_router(temp_key_router)
app.include_router(contact_router)
app.include_router(waitlist_router)
app.include_router(webhook_router)
app.include_router(auth_router)


@app.get("/health", tags=["ops"])
def health() -> dict:
    """Liveness / readiness probe."""
    db_ok = ping_db()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "psps": list(PSP_CLIENTS.keys()),
        "strategy": os.getenv("PSP_SELECTION_STRATEGY", "weighted_score"),
    }
