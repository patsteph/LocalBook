"""This Mac's sync state (LB-12): paired devices, pairing requests, previews.

`<data dir>/sync/sync.db` — PER-MACHINE, never synced (registry: `sync/` is
local). Who this Mac trusts is not something another Mac gets to decide.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    name TEXT,
    cert_pem TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    role TEXT,                 -- 'seed' (this Mac joined it) | 'joiner' (it joined this Mac)
    mode TEXT NOT NULL DEFAULT 'preview',   -- preview | live
    recovery_pub TEXT,
    paired_at REAL,
    last_seen REAL,
    last_error TEXT,
    revoked_at REAL
);
CREATE TABLE IF NOT EXISTS pairing (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,   -- 'incoming' (a Mac asked to pair with this one) | 'outgoing'
    device_id TEXT NOT NULL,
    name TEXT,
    cert_pem TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    sas TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    recovery_pub TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS previews (
    device_id TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def path() -> Path:
    from config import settings

    return Path(settings.data_dir) / "sync" / "sync.db"


def conn() -> sqlite3.Connection:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    c.executescript(SCHEMA)
    return c


def get(key: str, default=None):
    row = conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(key: str, value) -> None:
    conn().execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value)))


def enabled() -> bool:
    return bool(get("enabled", False))


def devices(include_revoked: bool = False) -> List[Dict[str, Any]]:
    q = "SELECT * FROM devices" + ("" if include_revoked else " WHERE revoked_at IS NULL")
    return [dict(r) for r in conn().execute(q + " ORDER BY paired_at")]


def device(device_id: str) -> Optional[Dict[str, Any]]:
    row = conn().execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    return dict(row) if row else None


def pin(d: Dict[str, Any], role: str) -> None:
    conn().execute(
        """INSERT INTO devices (device_id, name, cert_pem, fingerprint, host, port, role, mode,
                                recovery_pub, paired_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'preview', ?, ?)
           ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, cert_pem=excluded.cert_pem,
             fingerprint=excluded.fingerprint, host=COALESCE(excluded.host, devices.host),
             port=COALESCE(excluded.port, devices.port), role=excluded.role,
             recovery_pub=excluded.recovery_pub, paired_at=excluded.paired_at, revoked_at=NULL""",
        (d["device_id"], d.get("name"), d["cert_pem"], d["fingerprint"], d.get("host"),
         d.get("port"), role, d.get("recovery_pub"), time.time()))


def update_device(device_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE devices SET {cols} WHERE device_id=?", [*fields.values(), device_id])


def pairing_requests(direction: Optional[str] = None) -> List[Dict[str, Any]]:
    cutoff = time.time() - 600
    c = conn()
    c.execute("DELETE FROM pairing WHERE created_at < ?", (cutoff,))
    q = "SELECT * FROM pairing" + (" WHERE direction=?" if direction else "") + " ORDER BY created_at"
    return [dict(r) for r in c.execute(q, (direction,) if direction else ())]


def add_pairing(req: Dict[str, Any]) -> None:
    conn().execute(
        """INSERT OR REPLACE INTO pairing (id, direction, device_id, name, cert_pem, fingerprint,
                                          sas, host, port, recovery_pub, created_at)
           VALUES (:id, :direction, :device_id, :name, :cert_pem, :fingerprint, :sas, :host,
                   :port, :recovery_pub, :created_at)""", req)


def take_pairing(pairing_id: str) -> Optional[Dict[str, Any]]:
    c = conn()
    row = c.execute("SELECT * FROM pairing WHERE id=?", (pairing_id,)).fetchone()
    c.execute("DELETE FROM pairing WHERE id=?", (pairing_id,))
    return dict(row) if row else None


def save_preview(device_id: str, report: Dict[str, Any]) -> None:
    conn().execute("INSERT OR REPLACE INTO previews (device_id, report_json, created_at) VALUES (?, ?, ?)",
                   (device_id, json.dumps(report), time.time()))


def preview(device_id: str) -> Optional[Dict[str, Any]]:
    row = conn().execute("SELECT report_json, created_at FROM previews WHERE device_id=?",
                         (device_id,)).fetchone()
    return {**json.loads(row[0]), "created_at": row[1]} if row else None
