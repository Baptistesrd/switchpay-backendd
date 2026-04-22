"""
Data access layer for SwitchPay.

Backend selection (resolved once at import time from DATABASE_URL):

  DATABASE_URL set   →  PostgreSQL via psycopg2 ThreadedConnectionPool
  DATABASE_URL unset →  SQLite (local development fallback, zero config)

All public function signatures are identical regardless of backend, so no
other file needs to change when switching between the two.

Thread safety:
  PostgreSQL — ThreadedConnectionPool handles concurrent access; each
               operation borrows a connection, commits/rolls back, and
               returns it to the pool.
  SQLite     — a module-level threading.Lock serialises all access on the
               single shared connection (unchanged from the original impl).

Idempotency TTL:
  Records older than IDEMPOTENCY_TTL_SECONDS are treated as expired.
  A periodic cleanup task in main.py purges them every 60 minutes.
"""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("switchpay.db")

IDEMPOTENCY_TTL_SECONDS: int = 86_400  # 24 hours

# ── Backend detection ─────────────────────────────────────────────────────────

# Render (and most PaaS) sometimes emit "postgres://" — psycopg2 requires
# the "postgresql://" scheme.  Normalise here so deployments just work.
_raw_url: Optional[str] = os.environ.get("DATABASE_URL", "").strip() or None
if _raw_url and _raw_url.startswith("postgres://"):
    _raw_url = "postgresql://" + _raw_url[len("postgres://"):]

DATABASE_URL: Optional[str] = _raw_url
_USE_PG: bool = bool(DATABASE_URL)

# SQL placeholder token differs between the two drivers.
_PH: str = "%s" if _USE_PG else "?"

# ── PostgreSQL setup ──────────────────────────────────────────────────────────

if _USE_PG:
    import psycopg2
    import psycopg2.pool

    _pool: psycopg2.pool.ThreadedConnectionPool = psycopg2.pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=10,
        dsn=DATABASE_URL,
    )
    logger.info("DB backend: PostgreSQL (pool minconn=2 maxconn=10)")

    @contextmanager
    def _get_conn():
        """Borrow a connection from the pool, commit on success, rollback on error."""
        conn = _pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _pool.putconn(conn)

# ── SQLite setup ──────────────────────────────────────────────────────────────

else:
    _DB_PATH: str = os.environ.get(
        "DB_PATH",
        str(Path(__file__).resolve().parent.parent.parent / "transactions.db"),
    )
    _sqlite_conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    _lock = threading.Lock()
    logger.info("DB backend: SQLite (%s)", _DB_PATH)

    @contextmanager
    def _get_conn():
        """Yield the shared SQLite connection under the module lock."""
        with _lock:
            try:
                yield _sqlite_conn
                _sqlite_conn.commit()
            except Exception:
                _sqlite_conn.rollback()
                raise

# ── Schema creation ───────────────────────────────────────────────────────────

def _create_schema() -> None:
    """Create tables and indexes if they do not already exist.

    SQL is dialect-specific: PostgreSQL uses BIGSERIAL; SQLite uses
    INTEGER PRIMARY KEY AUTOINCREMENT.  Everything else is ANSI-compatible.
    """
    if _USE_PG:
        serial = "BIGSERIAL"
    else:
        serial = "INTEGER"  # SQLite: INTEGER PRIMARY KEY is auto-increment

    with _get_conn() as conn:
        cur = conn.cursor()

        if not _USE_PG:
            # SQLite performance pragmas (ignored on PostgreSQL).
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA foreign_keys=ON;")

        cur.execute(f"""
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

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS idempotency (
                key               TEXT PRIMARY KEY,
                request_hash      TEXT,
                tx_id             TEXT,
                response_snapshot TEXT,
                created_at        TEXT
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS contact_messages (
                id         {serial} PRIMARY KEY,
                email      TEXT NOT NULL,
                message    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS waitlist (
                id         {serial} PRIMARY KEY,
                email      TEXT NOT NULL UNIQUE,
                company    TEXT,
                role       TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_created_at ON transactions(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_psp        ON transactions(psp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_status     ON transactions(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_entreprise ON transactions(entreprise)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_idemp_created ON idempotency(created_at)")


_create_schema()

# ── Internal helpers ──────────────────────────────────────────────────────────

def _row_to_dict(cursor, row: tuple) -> dict:
    """Build a dict from a cursor row using column names from cursor.description.

    Works identically for both sqlite3.Cursor and psycopg2.cursor since both
    expose a .description attribute whose items have the column name at [0].
    """
    return dict(zip([col[0] for col in cursor.description], row))


# ── Transactions ──────────────────────────────────────────────────────────────

def save_transaction(tx: dict) -> None:
    """Persist or overwrite a transaction record."""
    raw = json.dumps(tx["raw_response"]) if tx.get("raw_response") is not None else None

    if _USE_PG:
        sql = """
            INSERT INTO transactions
                (id, entreprise, montant, devise, pays, psp, psp_tx_id,
                 device, created_at, status, latency_ms, raw_response)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET
                entreprise   = EXCLUDED.entreprise,
                montant      = EXCLUDED.montant,
                devise       = EXCLUDED.devise,
                pays         = EXCLUDED.pays,
                psp          = EXCLUDED.psp,
                psp_tx_id    = EXCLUDED.psp_tx_id,
                device       = EXCLUDED.device,
                created_at   = EXCLUDED.created_at,
                status       = EXCLUDED.status,
                latency_ms   = EXCLUDED.latency_ms,
                raw_response = EXCLUDED.raw_response
        """
    else:
        sql = """
            INSERT OR REPLACE INTO transactions
                (id, entreprise, montant, devise, pays, psp, psp_tx_id,
                 device, created_at, status, latency_ms, raw_response)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """

    params = (
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
        raw,
    )

    with _get_conn() as conn:
        conn.cursor().execute(sql, params)


def get_transaction_by_id(tx_id: str) -> Optional[dict]:
    """Return a single transaction by primary key, or None."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM transactions WHERE id = {_PH}", (tx_id,))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None


def get_all_transactions() -> list:
    """Return all transactions ordered newest-first."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions ORDER BY created_at DESC")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]


def get_psp_metrics() -> dict:
    """Return per-PSP aggregated metrics and overall totals via SQL.

    Both queries run inside a single borrowed connection so the summary
    totals are consistent with the per-PSP rows.
    """
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(psp, 'unknown')                             AS psp,
                COUNT(*)                                             AS transaction_count,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                AVG(latency_ms)                                      AS avg_latency_ms,
                SUM(COALESCE(montant, 0.0))                          AS total_volume
            FROM transactions
            GROUP BY psp
        """)
        psp_rows = cur.fetchall()

        cur.execute("SELECT COUNT(*), SUM(COALESCE(montant, 0.0)) FROM transactions")
        total_count, total_volume = cur.fetchone()

    by_psp: dict = {}
    for psp, tx_count, success_count, avg_latency, vol in psp_rows:
        auth_rate = success_count / tx_count if tx_count else 0.0
        by_psp[psp] = {
            "transaction_count":  tx_count,
            "success_count":      success_count,
            "authorization_rate": round(auth_rate, 4),
            "avg_latency_ms":     round(avg_latency, 1) if avg_latency is not None else None,
            "total_volume":       round(vol or 0.0, 2),
        }

    return {
        "summary": {
            "total_transactions": total_count or 0,
            "total_volume":       round(total_volume or 0.0, 2),
        },
        "by_psp": by_psp,
    }


def get_transactions_by_org(org: str) -> list:
    """Return all transactions for a specific organisation, newest-first."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM transactions WHERE entreprise = {_PH} ORDER BY created_at DESC",
            (org,),
        )
        return [_row_to_dict(cur, r) for r in cur.fetchall()]


def get_recent_transactions(limit: int) -> list:
    """Return the most recent ``limit`` transactions (newest-first)."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT * FROM transactions ORDER BY created_at DESC LIMIT {_PH}",
            (limit,),
        )
        return [_row_to_dict(cur, r) for r in cur.fetchall()]


# ── Idempotency ───────────────────────────────────────────────────────────────

def save_idempotency(
    key: str, request_hash: str, tx_id: str, response_snapshot: dict
) -> Optional[dict]:
    """Persist an idempotency record atomically and return the canonical row.

    Uses INSERT … ON CONFLICT DO NOTHING (PG) / INSERT OR IGNORE (SQLite) so
    that concurrent requests with the same key produce exactly one winner.
    Both callers then read the same canonical record from the same connection.
    """
    snap = json.dumps(response_snapshot)
    created = datetime.now(timezone.utc).isoformat()

    if _USE_PG:
        insert_sql = """
            INSERT INTO idempotency (key, request_hash, tx_id, response_snapshot, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (key) DO NOTHING
        """
    else:
        insert_sql = """
            INSERT OR IGNORE INTO idempotency
                (key, request_hash, tx_id, response_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?)
        """

    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(insert_sql, (key, request_hash, tx_id, snap, created))
        cur.execute(
            f"SELECT key, request_hash, tx_id, response_snapshot, created_at "
            f"FROM idempotency WHERE key = {_PH}",
            (key,),
        )
        row = cur.fetchone()

    if not row:
        return None

    k, req_hash, stored_tx_id, stored_snap, created_at_str = row
    return {
        "key":               k,
        "request_hash":      req_hash,
        "tx_id":             stored_tx_id,
        "response_snapshot": json.loads(stored_snap) if stored_snap else None,
        "created_at":        created_at_str,
    }


def get_idempotency(key: str) -> Optional[dict]:
    """Retrieve an idempotency record if it exists and has not expired (TTL = 24 h)."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT key, request_hash, tx_id, response_snapshot, created_at "
            f"FROM idempotency WHERE key = {_PH}",
            (key,),
        )
        row = cur.fetchone()

    if not row:
        return None

    k, req_hash, tx_id, snap, created_at_str = row

    try:
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created_at).total_seconds() > IDEMPOTENCY_TTL_SECONDS:
            logger.debug("Idempotency key expired: %s", key)
            return None
    except (ValueError, TypeError):
        logger.warning("Idempotency key %r has unparseable created_at=%r", key, created_at_str)
        return None

    return {
        "key":               k,
        "request_hash":      req_hash,
        "tx_id":             tx_id,
        "response_snapshot": json.loads(snap) if snap else None,
        "created_at":        created_at_str,
    }


def cleanup_expired_idempotency() -> int:
    """Delete idempotency records older than IDEMPOTENCY_TTL_SECONDS.

    Called every 60 minutes by the background task in main.py.

    Returns:
        Number of rows deleted.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=IDEMPOTENCY_TTL_SECONDS)
    ).isoformat()

    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM idempotency WHERE created_at < {_PH}", (cutoff,)
        )
        deleted = cur.rowcount

    if deleted:
        logger.info("Cleaned up %d expired idempotency records", deleted)
    return deleted


# ── Contact messages ──────────────────────────────────────────────────────────

def save_contact_message(email: str, message: str) -> None:
    """Persist a contact-form submission."""
    with _get_conn() as conn:
        conn.cursor().execute(
            f"INSERT INTO contact_messages (email, message, created_at) VALUES ({_PH},{_PH},{_PH})",
            (email, message, datetime.now(timezone.utc).isoformat()),
        )


def get_all_contact_messages() -> list:
    """Return all contact messages ordered newest-first."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM contact_messages ORDER BY created_at DESC")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]


# ── Waitlist ──────────────────────────────────────────────────────────────────

def save_waitlist(email: str, company: Optional[str], role: Optional[str]) -> None:
    """Add an email to the waitlist (silently ignores duplicates)."""
    if _USE_PG:
        sql = """
            INSERT INTO waitlist (email, company, role, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO NOTHING
        """
    else:
        sql = "INSERT OR IGNORE INTO waitlist (email, company, role, created_at) VALUES (?,?,?,?)"

    with _get_conn() as conn:
        conn.cursor().execute(
            sql,
            (email, company, role, datetime.now(timezone.utc).isoformat()),
        )


def get_waitlist() -> list:
    """Return all waitlist entries ordered newest-first."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM waitlist ORDER BY created_at DESC")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]


# ── Connectivity probe ────────────────────────────────────────────────────────

def ping_db() -> bool:
    """Return True if the DB can execute a trivial query."""
    try:
        with _get_conn() as conn:
            conn.cursor().execute("SELECT 1")
        return True
    except Exception:
        return False
