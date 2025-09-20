# backend/db/db_utils.py 
import sqlite3
import json
from datetime import datetime
from typing import Optional

conn = sqlite3.connect("transactions.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("PRAGMA journal_mode=WAL;")
cursor.execute("PRAGMA synchronous=NORMAL;")
cursor.execute("PRAGMA foreign_keys=ON;")

cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    entreprise TEXT,
    montant REAL,
    devise TEXT,
    pays TEXT,
    psp TEXT,
    psp_tx_id TEXT,
    device TEXT,
    created_at TEXT,
    status TEXT,
    latency_ms REAL,
    raw_response TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    request_hash TEXT,
    tx_id TEXT,
    response_snapshot TEXT,
    created_at TEXT
)
''')

cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_created_at ON transactions(created_at)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_psp ON transactions(psp)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_idemp_created_at ON idempotency(created_at)")

conn.commit()

def save_transaction(tx: dict):
    cursor.execute('''
        INSERT OR REPLACE INTO transactions (
            id, entreprise, montant, devise, pays, psp, psp_tx_id, device, created_at, status, latency_ms, raw_response
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        tx["id"],
        tx.get("entreprise"),
        tx.get("montant"),
        tx.get("devise"),
        tx.get("pays"),
        tx.get("psp"),
        tx.get("psp_tx_id"),
        tx.get("device"),
        tx.get("created_at"),
        tx.get("status"),
        tx.get("latency_ms"),
        json.dumps(tx.get("raw_response")) if tx.get("raw_response") is not None else None,
    ))
    conn.commit()

def get_transaction_by_id(tx_id: str) -> Optional[dict]:
    cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
    row = cursor.fetchone()
    if row:
        return dict(zip([c[0] for c in cursor.description], row))
    return None

def get_all_transactions() -> list[dict]:
    cursor.execute("SELECT * FROM transactions ORDER BY datetime(created_at) DESC")
    rows = cursor.fetchall()
    return [dict(zip([c[0] for c in cursor.description], row)) for row in rows]

def save_idempotency(key: str, request_hash: str, tx_id: str, response_snapshot: dict):
    cursor.execute('''
        INSERT OR REPLACE INTO idempotency (key, request_hash, tx_id, response_snapshot, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (key, request_hash, tx_id, json.dumps(response_snapshot), datetime.utcnow().isoformat()))
    conn.commit()

def get_idempotency(key: str) -> Optional[dict]:
    cursor.execute("SELECT key, request_hash, tx_id, response_snapshot, created_at FROM idempotency WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row:
        k, req_hash, tx_id, snap, created_at = row
        return {
            "key": k,
            "request_hash": req_hash,
            "tx_id": tx_id,
            "response_snapshot": json.loads(snap) if snap else None,
            "created_at": created_at
        }
    return None
