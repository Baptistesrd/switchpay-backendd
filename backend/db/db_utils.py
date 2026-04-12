"""
SQLite data access layer for SwitchPay.

Thread safety: a module-level threading.Lock serialises all DB writes and
reads.  A single shared connection is used with WAL journal mode, which
allows concurrent readers but serialised writers — sufficient for the current
traffic level.  Upgrade to a connection pool (e.g. aiosqlite) before scaling
to multiple workers.

Idempotency TTL: records older than IDEMPOTENCY_TTL_SECONDS are treated as
expired and ignored by get_idempotency().  A cleanup sweep runs on import.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("switchpay.db")

IDEMPOTENCY_TTL_SECONDS: int = 86_400  # 24 hours

_lock = threading.Lock()
_conn = sqlite3.connect("transactions.db", check_same_thread=False)

# ── Schema setup ─────────────────────────────────────────────────────────────

with _lock:
    _conn.execute("PRAGMA journal_mode=WAL;")
    _conn.execute("PRAGMA synchronous=NORMAL;")
    _conn.execute("PRAGMA foreign_keys=ON;")

    _conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id          TEXT PRIMARY KEY,
            entreprise  TEXT,
            montant     REAL,
            devise      TEXT,
            pays        TEXT,
            psp         TEXT,
            psp_tx_id   TEXT,
            device      TEXT,
            created_at  TEXT,
            status      TEXT,
            latency_ms  REAL,
            raw_response TEXT
        )
    """)

    _conn.execute("""
        CREATE TABLE IF NOT EXISTS idempotency (
            key               TEXT PRIMARY KEY,
            request_hash      TEXT,
            tx_id             TEXT,
            response_snapshot TEXT,
            created_at        TEXT
        )
    """)

    _conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    _conn.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT NOT NULL UNIQUE,
            company    TEXT,
            role       TEXT,
            created_at TEXT NOT NULL
        )
    """)

    _conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_created_at  ON transactions(created_at)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_psp         ON transactions(psp)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_status      ON transactions(status)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_entreprise  ON transactions(entreprise)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_idemp_created  ON idempotency(created_at)")
    _conn.commit()


# ── Internal helpers ─────────────────────────────────────────────────────────

def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return dict(zip([c[0] for c in cursor.description], row))


# ── Transactions ─────────────────────────────────────────────────────────────

def save_transaction(tx: dict) -> None:
    """Persist or overwrite a transaction record."""
    with _lock:
        _conn.execute(
            """
            INSERT OR REPLACE INTO transactions
                (id, entreprise, montant, devise, pays, psp, psp_tx_id,
                 device, created_at, status, latency_ms, raw_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
                json.dumps(tx["raw_response"]) if tx.get("raw_response") is not None else None,
            ),
        )
        _conn.commit()


def get_transaction_by_id(tx_id: str) -> Optional[dict]:
    """Return a single transaction by primary key, or None."""
    with _lock:
        cur = _conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None


def get_all_transactions() -> list:
    """Return all transactions ordered newest-first."""
    with _lock:
        cur = _conn.execute(
            "SELECT * FROM transactions ORDER BY datetime(created_at) DESC"
        )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


def get_psp_metrics() -> dict:
    """Return per-PSP aggregated metrics and overall totals via SQL.

    Runs two queries under a single lock so the summary totals are consistent
    with the per-PSP rows.  No application-side iteration over individual rows.

    Returns:
        Dict with:
          "summary": {"total_transactions": int, "total_volume": float}
          "by_psp":  {psp: {transaction_count, success_count,
                             authorization_rate, avg_latency_ms, total_volume}}
    """
    with _lock:
        cur = _conn.execute("""
            SELECT
                COALESCE(psp, 'unknown')                                AS psp,
                COUNT(*)                                                AS transaction_count,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)    AS success_count,
                AVG(latency_ms)                                         AS avg_latency_ms,
                SUM(COALESCE(montant, 0.0))                             AS total_volume
            FROM transactions
            GROUP BY psp
        """)
        psp_rows = cur.fetchall()

        cur2 = _conn.execute(
            "SELECT COUNT(*), SUM(COALESCE(montant, 0.0)) FROM transactions"
        )
        total_count, total_volume = cur2.fetchone()

    by_psp = {}
    for psp, tx_count, success_count, avg_latency, vol in psp_rows:
        auth_rate = success_count / tx_count if tx_count else 0.0
        by_psp[psp] = {
            "transaction_count": tx_count,
            "success_count": success_count,
            "authorization_rate": round(auth_rate, 4),
            "avg_latency_ms": round(avg_latency, 1) if avg_latency is not None else None,
            "total_volume": round(vol or 0.0, 2),
        }

    return {
        "summary": {
            "total_transactions": total_count or 0,
            "total_volume": round(total_volume or 0.0, 2),
        },
        "by_psp": by_psp,
    }


def get_transactions_by_org(org: str) -> list:
    """Return all transactions for a specific organisation, newest-first."""
    with _lock:
        cur = _conn.execute(
            "SELECT * FROM transactions WHERE entreprise = ? ORDER BY datetime(created_at) DESC",
            (org,),
        )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


def get_recent_transactions(limit: int) -> list:
    """Return the most recent ``limit`` transactions (newest-first).

    Performs the LIMIT in SQL to avoid loading the entire table into memory —
    important for the scoring engine which only needs HISTORY_WINDOW rows.

    Args:
        limit: Maximum number of rows to return.

    Returns:
        List of transaction dicts, most recent first.
    """
    with _lock:
        cur = _conn.execute(
            "SELECT * FROM transactions ORDER BY datetime(created_at) DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


# ── Idempotency ───────────────────────────────────────────────────────────────

def save_idempotency(
    key: str, request_hash: str, tx_id: str, response_snapshot: dict
) -> Optional[dict]:
    """Persist an idempotency record atomically and return the canonical row.

    Uses INSERT OR IGNORE so that if two concurrent requests race with the same
    key, exactly one write wins and the loser's insert is silently discarded.
    Both callers then read the same canonical record in the same lock, so the
    caller that lost the race can detect it and return the winning response.
    """
    with _lock:
        _conn.execute(
            """
            INSERT OR IGNORE INTO idempotency
                (key, request_hash, tx_id, response_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                key,
                request_hash,
                tx_id,
                json.dumps(response_snapshot),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _conn.commit()
        cur = _conn.execute(
            "SELECT key, request_hash, tx_id, response_snapshot, created_at "
            "FROM idempotency WHERE key = ?",
            (key,),
        )
        row = cur.fetchone()

    if not row:
        return None
    k, req_hash, stored_tx_id, snap, created_at_str = row
    return {
        "key": k,
        "request_hash": req_hash,
        "tx_id": stored_tx_id,
        "response_snapshot": json.loads(snap) if snap else None,
        "created_at": created_at_str,
    }


def get_idempotency(key: str) -> Optional[dict]:
    """Retrieve an idempotency record if it exists and has not expired.

    Records older than IDEMPOTENCY_TTL_SECONDS are treated as non-existent,
    preventing stale keys from blocking legitimate retries after 24 hours.

    Args:
        key: The idempotency key header value.

    Returns:
        Dict with keys: key, request_hash, tx_id, response_snapshot, created_at.
        None if not found or expired.
    """
    with _lock:
        cur = _conn.execute(
            "SELECT key, request_hash, tx_id, response_snapshot, created_at "
            "FROM idempotency WHERE key = ?",
            (key,),
        )
        row = cur.fetchone()

    if not row:
        return None

    k, req_hash, tx_id, snap, created_at_str = row

    # TTL enforcement
    try:
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        if age > IDEMPOTENCY_TTL_SECONDS:
            logger.debug("Idempotency key expired: %s (age=%.0fs)", key, age)
            return None
    except (ValueError, TypeError):
        pass  # unparseable timestamp — treat record as valid

    return {
        "key": k,
        "request_hash": req_hash,
        "tx_id": tx_id,
        "response_snapshot": json.loads(snap) if snap else None,
        "created_at": created_at_str,
    }


def cleanup_expired_idempotency() -> int:
    """Delete idempotency records older than IDEMPOTENCY_TTL_SECONDS.

    Should be called periodically (e.g. on application startup or via a
    background task) to prevent unbounded table growth.

    Returns:
        Number of rows deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=IDEMPOTENCY_TTL_SECONDS)
    ).isoformat()
    with _lock:
        cur = _conn.execute(
            "DELETE FROM idempotency WHERE created_at < ?", (cutoff,)
        )
        _conn.commit()
        deleted = cur.rowcount
    if deleted:
        logger.info("Cleaned up %d expired idempotency records", deleted)
    return deleted


# ── Contact messages ──────────────────────────────────────────────────────────

def save_contact_message(email: str, message: str) -> None:
    """Persist a contact-form submission."""
    with _lock:
        _conn.execute(
            "INSERT INTO contact_messages (email, message, created_at) VALUES (?, ?, ?)",
            (email, message, datetime.now(timezone.utc).isoformat()),
        )
        _conn.commit()


def get_all_contact_messages() -> list:
    """Return all contact messages ordered newest-first."""
    with _lock:
        cur = _conn.execute(
            "SELECT * FROM contact_messages ORDER BY datetime(created_at) DESC"
        )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


# ── Waitlist ──────────────────────────────────────────────────────────────────

def save_waitlist(email: str, company: Optional[str], role: Optional[str]) -> None:
    """Add an email to the waitlist (silently ignores duplicates)."""
    with _lock:
        _conn.execute(
            """
            INSERT OR IGNORE INTO waitlist (email, company, role, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (email, company, role, datetime.now(timezone.utc).isoformat()),
        )
        _conn.commit()


def get_waitlist() -> list:
    """Return all waitlist entries ordered newest-first."""
    with _lock:
        cur = _conn.execute(
            "SELECT * FROM waitlist ORDER BY datetime(created_at) DESC"
        )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


# ── Startup cleanup ───────────────────────────────────────────────────────────
cleanup_expired_idempotency()
