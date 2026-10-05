"""Archival memory's TEXT as SQLite records — LanceDB becomes derived (LB-12 D2).

Before this, a long-term memory existed only as a LanceDB row: its words and its
vector together, in a store with no change journal, so it could not sync and a
rebuilt index could not recover it. Now the record (`archival_records` in
recall_memory.db) is the truth and syncs like any other row; the LanceDB table
is an index over it, kept in step by `reconcile()` — after a sync, records
another Mac wrote are embedded here, and vectors whose record was deleted go.

Beside `memory_store` (past 1200 lines); it calls in at add, delete, startup.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

SCHEMA = """CREATE TABLE IF NOT EXISTS archival_records (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'system',
    content TEXT NOT NULL,
    content_type TEXT,
    source_type TEXT,
    source_id TEXT,
    source_notebook_id TEXT,
    topics TEXT,
    entities TEXT,
    importance TEXT,
    created_at TEXT NOT NULL
)"""
COLUMNS = ("id", "namespace", "content", "content_type", "source_type", "source_id",
           "source_notebook_id", "topics", "entities", "importance", "created_at")


def ensure(conn) -> None:
    conn.execute(SCHEMA)


def write(conn, record: Dict[str, Any]) -> None:
    """Insert or replace one record (no `vector`, no access counters)."""
    vals = [record.get(c) for c in COLUMNS]
    conn.execute(f"INSERT INTO archival_records ({', '.join(COLUMNS)}) VALUES "
                 f"({', '.join('?' * len(COLUMNS))}) ON CONFLICT(id) DO UPDATE SET "
                 + ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id"), vals)


def delete(conn, ids: Iterable[str]) -> int:
    ids = [str(i) for i in ids or []]
    n = 0
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        n += conn.execute(f"DELETE FROM archival_records WHERE id IN ({','.join('?' * len(part))})",
                          part).rowcount or 0
    return n


def backfill(store) -> int:
    """Once: every LanceDB archival row that has no record gets one."""
    conn = store._get_recall_connection()
    try:
        ensure(conn)
        have = {r[0] for r in conn.execute("SELECT id FROM archival_records")}
        if "archival_memories" not in store.archival_db.table_names():
            return 0
        df = store.archival_db.open_table("archival_memories").to_pandas()
        n = 0
        for _, row in df.iterrows():
            if row["id"] in have:
                continue
            write(conn, {c: (row.get(c) if c in row else None) for c in COLUMNS})
            n += 1
        conn.commit()
        if n:
            logger.info("[archival] backfilled %d records from LanceDB", n)
        return n
    finally:
        conn.close()


def reconcile(store) -> Dict[str, int]:
    """Make the LanceDB index match the records: embed what is missing (a Mac
    that synced records in), drop what has no record (deleted elsewhere).
    Blocking — the embedding model runs here; call it from a background job."""
    from models.memory import ArchivalMemoryEntry, MemoryImportance, MemorySourceType

    conn = store._get_recall_connection()
    try:
        ensure(conn)
        conn.row_factory = None
        records = {r[0]: dict(zip(COLUMNS, r)) for r in conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM archival_records")}
    finally:
        conn.close()
    table = store.archival_db.open_table("archival_memories")
    indexed = set(table.to_pandas()["id"].tolist()) if table.count_rows() else set()

    added = 0
    for rid in sorted(records.keys() - indexed):
        r = records[rid]
        try:
            entry = ArchivalMemoryEntry(
                id=rid, content=r["content"], content_type=r.get("content_type") or "note",
                source_type=MemorySourceType(r.get("source_type") or "system"),
                source_id=r.get("source_id"), source_notebook_id=r.get("source_notebook_id"),
                topics=json.loads(r.get("topics") or "[]"), entities=json.loads(r.get("entities") or "[]"),
                importance=MemoryImportance(r.get("importance") or "medium"),
            )
            store._index_archival(entry, r.get("namespace") or "system",
                                  r.get("source_notebook_id") or "", created_at=r.get("created_at"))
            added += 1
        except Exception as exc:
            logger.warning("[archival] could not index record %s: %s", rid, exc)

    gone = sorted(indexed - records.keys())
    for i in range(0, len(gone), 200):
        part = gone[i:i + 200]
        table.delete("id IN (" + ", ".join(f"'{x}'" for x in part) + ")")
    if gone:
        store.delete_fts(gone)
    return {"indexed": added, "dropped": len(gone)}
