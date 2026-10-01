"""Process-wide sync plumbing (LB-12): the clock, the databases, the journals.

The engine is transport-agnostic; this is where it meets the real data dir.
Engine work is blocking SQLite, so every entry point here is sync and is run
with `asyncio.to_thread` by the callers — never on the request loop.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Dict, Optional

from services.sync import engine, journal
from services.sync.hlc import Clock

_clock: Optional[Clock] = None
_lock = threading.Lock()
# One engine operation at a time on this Mac: a pull applying while a push
# exports would interleave BEGIN IMMEDIATEs with nothing gained.
engine_lock = threading.Lock()


def data_dir() -> Path:
    from config import settings

    return Path(settings.data_dir)


DB_FILES = {"main": "localbook.db", "recall": "memory/recall_memory.db"}


def clock() -> Clock:
    global _clock
    with _lock:
        if _clock is None:
            from services.sync import identity

            _clock = Clock(identity.device_id())
        return _clock


def replica(db: str) -> engine.Replica:
    """A fresh engine connection to one database (foreign keys OFF — see Replica)."""
    from services.sync import identity

    path = data_dir() / DB_FILES[db]
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA busy_timeout=10000")
    return engine.Replica(conn, db, identity.device_id(), clock(), data_dir=data_dir())


def install_journals() -> Dict[str, list]:
    """Triggers + journal tables in both databases. Idempotent; run when sync
    is turned on and at every loop start (lazily created stores get covered)."""
    out = {}
    for db in DB_FILES:
        path = data_dir() / DB_FILES[db]
        if not path.exists():
            continue
        conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
        try:
            out[db] = journal.install(conn, db)
        finally:
            conn.close()
    return out


def ledger_head() -> int:
    from services import migration_ledger

    return int(migration_ledger.head())


def vvs() -> Dict[str, Dict[str, int]]:
    out = {}
    for db in DB_FILES:
        r = replica(db)
        try:
            journal.install(r.conn, db)
            out[db] = engine.vv(r.conn)
        finally:
            r.conn.close()
    return out
