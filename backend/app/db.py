"""SQLite storage for chat history.

We use Python's built-in sqlite3 module (no ORM) to keep things simple.
A new connection is opened per operation, which is safe across threads and
cheap for SQLite.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,          -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    metadata    TEXT,                   -- JSON string, assistant messages only
    created_at  TEXT NOT NULL           -- server time, ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

-- Extra table: remembers a custom system prompt per session.
-- It is never returned by the history endpoint.
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    system_prompt  TEXT,
    created_at     TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(settings.database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the folder and tables on startup (idempotent)."""
    folder = os.path.dirname(settings.database_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def ping() -> bool:
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def get_history(session_id: str) -> list[dict]:
    """All messages of a session, oldest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, metadata, created_at FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    history = []
    for row in rows:
        item = {"role": row["role"], "content": row["content"], "created_at": row["created_at"]}
        if row["metadata"]:
            item["metadata"] = json.loads(row["metadata"])
        history.append(item)
    return history


def get_session_system_prompt(session_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row["system_prompt"] if row else None


def save_exchange(
    session_id: str,
    user_message: str,
    reply: str,
    metadata: dict,
    system_prompt: str | None,
) -> None:
    """Store the user message + assistant reply together in ONE transaction.

    This is only called after the LLM succeeded, so failed requests (422/503)
    never reach the history.
    """
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt)",
            (session_id, system_prompt, now),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) "
            "VALUES (?, 'user', ?, NULL, ?)",
            (session_id, user_message, now),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, reply, json.dumps(metadata), _now()),
        )
