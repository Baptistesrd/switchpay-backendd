import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers.contact import router as contact_router
from backend.routers.metrics import router as metrics_router
from backend.routers.temp_key_router import limiter, router as temp_key_router
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from backend.routers.transaction import router as transaction_router
from backend.routers.waitlist import router as waitlist_router
from backend.routers.webhook import router as webhook_router
from contextlib import asynccontextmanager

from backend.services.payment_processor import PSP_CLIENTS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("switchpay")


@asynccontextmanager
async def lifespan(application: "FastAPI"):  # type: ignore[name-defined]
    logger.info("SwitchPay API starting up")
    yield
    logger.info("SwitchPay API shut down")


app = FastAPI(
    title="SwitchPay API",
    version="2.1.0",
    description="Dynamic payment routing — selects the optimal PSP per transaction.",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://switchpayglobal.com",
        "https://switchpay-frontendd.onrender.com",
    ],
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


@app.get("/health", tags=["ops"])
def health() -> dict:
    """Liveness / readiness probe.

    Returns the API status, registered PSP clients, and active routing strategy
    so the frontend dashboard can reflect the current configuration.
    """
    from backend.db.db_utils import _conn  # light connectivity check

    db_ok = False
    try:
        _conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "psps": list(PSP_CLIENTS.keys()),
        "strategy": os.getenv("PSP_SELECTION_STRATEGY", "weighted_score"),
    }
