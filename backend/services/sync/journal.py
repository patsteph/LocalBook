"""Change capture (LB-12c): the journal tables and the triggers that feed them.

Triggers, not call sites: `get_db()` alone has ~190 call sites, and a writer that
forgets to journal is a change that never syncs. Every synced table gets three
triggers that append (table, key, op) to `_changes`.

⚠️ Built-in SQL only. A trigger that calls a custom function or reads a temp
table FAILS THE WRITE on any connection that did not register it — the app has
several, plus scripts and DB browsers. `json_array` and `CURRENT_TIMESTAMP` are
built in. The HLC is assigned later, in Python, by the shipper.

The sync bookkeeping lives in the SAME file as the data it describes, so a
remote apply and its bookkeeping commit in one transaction (atomic commit across
ATTACHed databases is not guaranteed in WAL mode).
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, List

from services.sync import registry

SCHEMA = """
CREATE TABLE IF NOT EXISTS _changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tbl TEXT NOT NULL,
    pk TEXT NOT NULL,
    op TEXT NOT NULL,
    changed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Seqs produced by applying a REMOTE change: the shipper skips them (echo
-- suppression without a connection-local flag the triggers would have to read).
CREATE TABLE IF NOT EXISTS _remote_applied (seq INTEGER PRIMARY KEY);
-- Per row: every field's clock, ancestry and value hash (hashes only — never a
-- second copy of a document).
CREATE TABLE IF NOT EXISTS _sync_rows (
    tbl TEXT NOT NULL, pk TEXT NOT NULL, meta TEXT NOT NULL,
    PRIMARY KEY (tbl, pk)
);
-- What peers can pull: one entry per row (coalesced), named by the write that
-- last changed it, so changes relay A→B→C without A and C ever meeting.
CREATE TABLE IF NOT EXISTS _sync_log (
    tbl TEXT NOT NULL, pk TEXT NOT NULL, origin TEXT NOT NULL, oseq INTEGER NOT NULL,
    PRIMARY KEY (tbl, pk)
);
CREATE INDEX IF NOT EXISTS _sync_log_origin ON _sync_log(origin, oseq);
-- This Mac's version vector: for each origin, every entry up to `seq` is here.
CREATE TABLE IF NOT EXISTS _sync_vv (origin TEXT PRIMARY KEY, seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS _sync_state (key TEXT PRIMARY KEY, value TEXT);
"""


def _pk_expr(prefix: str, pk: Iterable[str]) -> str:
    return "json_array(" + ", ".join(f"{prefix}.\"{c}\"" for c in pk) + ")"


def columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def synced_columns(conn: sqlite3.Connection, t: registry.Table) -> List[str]:
    return [c for c in columns(conn, t.name) if c not in t.exclude]


def trigger_names(table: str) -> List[str]:
    return [f"_sync_{table}_ai", f"_sync_{table}_au", f"_sync_{table}_ad"]


def ensure_uid(conn: sqlite3.Connection, table: str) -> None:
    """A log table's cross-Mac identity: a `uid` column, unique, filled by
    SQLite on every insert with built-in `randomblob` (never a custom function),
    and backfilled once for rows that predate it."""
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    if "uid" not in cols:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN uid TEXT')
    conn.execute(f'UPDATE "{table}" SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL')
    conn.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "_uid_{table}" ON "{table}"(uid)')
    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS "_uid_{table}_fill" AFTER INSERT ON "{table}"
        WHEN NEW.uid IS NULL BEGIN
            UPDATE "{table}" SET uid = lower(hex(randomblob(16))) WHERE rowid = NEW.rowid;
        END""")


def install(conn: sqlite3.Connection, db: str) -> List[str]:
    """Create the journal tables and every trigger for tables that exist now.
    Idempotent; returns the tables newly covered (lazily created stores get
    theirs on a later call)."""
    conn.executescript(SCHEMA)
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in registry.tables(db):
        if t.uid and t.name in existing:
            ensure_uid(conn, t.name)
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    added = []
    for t in registry.tables(db):
        if t.name not in existing:
            continue
        cols = synced_columns(conn, t)
        ai, au, ad = trigger_names(t.name)
        new, old = _pk_expr("NEW", t.pk), _pk_expr("OLD", t.pk)
        watched = ", ".join(f'"{c}"' for c in cols)
        stmts = []
        # The UPDATE trigger names its columns; a column added later (the app
        # evolves by ALTER TABLE) would go unjournaled. Recreate it when the
        # synced column set changes.
        sig_key = f"cols:{t.name}"
        row = conn.execute("SELECT value FROM _sync_state WHERE key=?", (sig_key,)).fetchone()
        if row is None or row[0] != watched:
            if au in have:
                conn.execute(f"DROP TRIGGER IF EXISTS {au}")
                have.discard(au)
            conn.execute("INSERT OR REPLACE INTO _sync_state(key, value) VALUES (?, ?)",
                         (sig_key, watched))
        if ai not in have:
            stmts.append(f"""CREATE TRIGGER IF NOT EXISTS {ai} AFTER INSERT ON "{t.name}" BEGIN
                INSERT INTO _changes(tbl, pk, op) VALUES ('{t.name}', {new}, 'i'); END;""")
        if au not in have:
            # `UPDATE OF` the synced columns only: counters and device-scoped
            # columns change often and never ship. A re-key journals both keys.
            stmts.append(f"""CREATE TRIGGER IF NOT EXISTS {au} AFTER UPDATE OF {watched} ON "{t.name}" BEGIN
                INSERT INTO _changes(tbl, pk, op) VALUES ('{t.name}', {new}, 'u');
                INSERT INTO _changes(tbl, pk, op) SELECT '{t.name}', {old}, 'd'
                    WHERE {old} IS NOT {new}; END;""")
        if ad not in have:
            stmts.append(f"""CREATE TRIGGER IF NOT EXISTS {ad} AFTER DELETE ON "{t.name}" BEGIN
                INSERT INTO _changes(tbl, pk, op) VALUES ('{t.name}', {old}, 'd'); END;""")
        if stmts:
            conn.executescript("\n".join(stmts))
            added.append(t.name)
    return added


def missing_triggers(conn: sqlite3.Connection, db: str) -> List[str]:
    """Synced tables that exist but lack any of their three triggers (coverage)."""
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    return [t.name for t in registry.tables(db)
            if t.name in existing and not set(trigger_names(t.name)) <= have]
