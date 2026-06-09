"""SQLite connection management + migrate-on-start.

Single-user local app, so a single shared connection guarded by a lock is
plenty. WAL mode keeps the read side (UI polling) from blocking writers.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ..paths import DB_PATH, ensure_dirs

_SCHEMA = Path(__file__).with_name("schema.sql")

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,  # guarded by _lock; pytapo work runs in a threadpool
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the shared connection and apply the schema. Idempotent."""
    global _conn
    with _lock:
        if _conn is None:
            ensure_dirs()
            _conn = _connect(db_path or DB_PATH)
            _conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
            _conn.commit()
        return _conn


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        return init_db()
    return _conn


def lock() -> threading.RLock:
    """Shared lock; callers must hold it for the duration of a transaction."""
    return _lock


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
