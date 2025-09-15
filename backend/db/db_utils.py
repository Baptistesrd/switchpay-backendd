import sqlite3, json
from datetime import datetime

conn = sqlite3.connect("transactions.db", check_same_thread=False)
cursor = conn.cursor()

# Active des options SQLite utiles
cursor.execute("PRAGMA journal_mode=WAL;")
cursor.execute("PRAGMA synchronous=NORMAL;")
cursor.execute("PRAGMA foreign_keys=ON;")

# === Transactions table ===
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
        latency_ms REAL  -- <— NEW
    )
''')

# Ajoute la colonne si la table existait déjà
try:
    cursor.execute("ALTER TABLE transactions ADD COLUMN latency_ms REAL")
except sqlite3.OperationalError:
    pass  # colonne déjà là

# Index utiles pour /metrics et /transactions
cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_created_at ON transactions(created_at)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_psp ON transactions(psp)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status)")
conn.commit()

def save_transaction(tx: dict):
    cursor.execute('''
        INSERT INTO transactions (
            id, entreprise, montant, devise, pays, psp, psp_tx_id, device, created_at, status, latency_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        tx["id"],
        tx["entreprise"],
        tx["montant"],
        tx["devise"],
        tx["pays"],
        tx["psp"],
        tx.get("psp_tx_id"),
        tx.get("device"),
        tx.get("created_at"),
        tx["status"],
        tx.get("latency_ms"),  # <— NEW
    ))
    conn.commit()

def get_transaction_by_id(tx_id: str) -> dict | None:
    cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
    row = cursor.fetchone()
    if row:
        return dict(zip([c[0] for c in cursor.description], row))
    return None

def get_all_transactions() -> list[dict]:
    cursor.execute("SELECT * FROM transactions ORDER BY datetime(created_at) DESC")  # <— tri récent d’abord
    rows = cursor.fetchall()
    return [dict(zip([c[0] for c in cursor.description], row)) for row in rows]


