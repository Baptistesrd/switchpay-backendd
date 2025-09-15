# backend/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers.transaction import router as transaction_router
from backend.routers.metrics import router as metrics_router

app = FastAPI(
    title="SwitchPay API",
    description="Smart router de paiements multi-PSP pour PME & marketplaces",
    version="2.0.0",
)

# 🧨 Dev: autorise tout (change en prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # ⚠️ à restreindre en prod (domaines front connus)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🔗 Routers métier
app.include_router(transaction_router)
app.include_router(metrics_router)

# 🔧 Health & Version
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"name": "SwitchPay API", "version": "2.0.0"}

