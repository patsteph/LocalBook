"""Cursor Routing Catalog Store — persist the per-notebook DERIVED routing catalog in app-data.

The catalog is built from the folder's own `schema.html` + `AGENTS.md` at connect/refresh and cached
here (keyed by notebook), NOT in the user's export folder — so nothing is hand-authored or copied per
monthly export. Mirrors the `tabular_store` convention: one SQLite under `settings.data_dir/tabular.db`,
lazy idempotent schema, never-raises. Cursor-only — the spreadsheet path never touches this table.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

from config import settings

logger = logging.getLogger(__name__)

_TABLE = "_cursor_routing_catalog"


def _db_path():
    return settings.data_dir / "tabular.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
            notebook_id        TEXT PRIMARY KEY,
            catalog_json       TEXT NOT NULL,
            schema_fingerprint TEXT,
            generated_at       TEXT NOT NULL
        )"""
    )
    return conn


def store_catalog(notebook_id: str, catalog: Dict[str, Any],
                  schema_fingerprint: str = "") -> bool:
    try:
        conn = _connect()
        try:
            conn.execute(
                f"INSERT INTO {_TABLE} (notebook_id, catalog_json, schema_fingerprint, generated_at) "
                f"VALUES (?,?,?,?) ON CONFLICT(notebook_id) DO UPDATE SET "
                f"catalog_json=excluded.catalog_json, schema_fingerprint=excluded.schema_fingerprint, "
                f"generated_at=excluded.generated_at",
                (notebook_id, json.dumps(catalog, default=str), schema_fingerprint or "",
                 datetime.utcnow().isoformat()),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[cursor_catalog_store] store failed ({notebook_id}): {e}")
        return False


def get_catalog(notebook_id: str) -> Optional[Dict[str, Any]]:
    try:
        conn = _connect()
        try:
            row = conn.execute(
                f"SELECT catalog_json FROM {_TABLE} WHERE notebook_id = ?", (notebook_id,)
            ).fetchone()
            return json.loads(row["catalog_json"]) if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[cursor_catalog_store] get failed ({notebook_id}): {e}")
        return None


def drop_catalog(notebook_id: str) -> None:
    try:
        conn = _connect()
        try:
            conn.execute(f"DELETE FROM {_TABLE} WHERE notebook_id = ?", (notebook_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[cursor_catalog_store] drop failed ({notebook_id}): {e}")
