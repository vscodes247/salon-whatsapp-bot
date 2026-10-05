"""SQLite storage: bookings, conversation logs, and webhook de-duplication.

Every function opens its own short-lived connection. That's the simplest way to
stay safe when FastAPI runs our handlers in a thread pool.
"""
import sqlite3
from contextlib import contextmanager
from typing import Optional

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id         TEXT NOT NULL,              -- customer's WhatsApp number, e.g. 919876543210
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content       TEXT NOT NULL,
    tool_name     TEXT,                       -- set for role='tool'; 'error' for failed replies
    latency_ms    INTEGER,                    -- set on assistant rows: LLM + tools time
    total_tokens  INTEGER,                    -- set on assistant rows: OpenAI tokens used
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))  -- UTC
);
CREATE INDEX IF NOT EXISTS idx_messages_wa_id ON messages (wa_id, id);

CREATE TABLE IF NOT EXISTS bookings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id          TEXT NOT NULL,
    customer_name  TEXT NOT NULL,
    service        TEXT NOT NULL,             -- service key, e.g. 'mens_haircut'
    date           TEXT NOT NULL,             -- YYYY-MM-DD (salon local time)
    time           TEXT NOT NULL,             -- HH:MM, 24h
    notes          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'confirmed',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_bookings_slot ON bookings (date, time);

-- Meta retries webhooks that it thinks failed; we remember message IDs to avoid double replies.
CREATE TABLE IF NOT EXISTS processed_messages (
    wa_message_id TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


@contextmanager
def get_conn():
    # isolation_level=None = autocommit; we start transactions manually where we need them.
    conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        conn.executescript(SCHEMA)


# ---------- de-duplication ----------

def mark_message_processed(wa_message_id: str) -> bool:
    """Returns True the first time we see this message ID, False for retries."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO processed_messages (wa_message_id) VALUES (?)",
            (wa_message_id,),
        )
        return cur.rowcount == 1


# ---------- conversation logs ----------

def log_message(
    wa_id: str,
    role: str,
    content: str,
    tool_name: Optional[str] = None,
    latency_ms: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (wa_id, role, content, tool_name, latency_ms, total_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (wa_id, role, content, tool_name, latency_ms, total_tokens),
        )


def get_history(wa_id: str, limit: int) -> list[dict]:
    """Last `limit` user/assistant messages, oldest first, in OpenAI chat format.

    Tool rows are logged for analysis but not replayed: the assistant's own text
    already records what happened (e.g. "Booked! Sat 5 PM").
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE wa_id = ? AND role IN ('user', 'assistant') "
            "ORDER BY id DESC LIMIT ?",
            (wa_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------- bookings ----------

def get_slot_counts(date: str) -> dict[str, int]:
    """{'10:00': 1, '14:00': 2, ...} for confirmed bookings on a date."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT time, COUNT(*) AS n FROM bookings "
            "WHERE date = ? AND status = 'confirmed' GROUP BY time",
            (date,),
        ).fetchall()
    return {r["time"]: r["n"] for r in rows}


def create_booking(
    wa_id: str,
    customer_name: str,
    service: str,
    date: str,
    time: str,
    notes: str,
    capacity: int,
) -> Optional[int]:
    """Insert a booking if the slot still has room. Returns booking id, or None if full.

    The capacity check and the insert happen inside one BEGIN IMMEDIATE transaction,
    so two customers racing for the last chair can't both get it.
    """
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")  # takes the write lock up front
        try:
            taken = conn.execute(
                "SELECT COUNT(*) FROM bookings WHERE date = ? AND time = ? AND status = 'confirmed'",
                (date, time),
            ).fetchone()[0]
            if taken >= capacity:
                conn.execute("ROLLBACK")
                return None
            cur = conn.execute(
                "INSERT INTO bookings (wa_id, customer_name, service, date, time, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (wa_id, customer_name, service, date, time, notes),
            )
            booking_id = cur.lastrowid
            conn.execute("COMMIT")
            return booking_id
        except Exception:
            conn.execute("ROLLBACK")
            raise
