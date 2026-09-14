"""One-shot purge of Cursor Style residue (feature removed in v2.3.0).

Deleting the code leaves DATA behind, and one row type is actively harmful rather than merely
untidy. `_tabular_catalog` holds a `cursor:<notebook>` row per external table/view, and
`tabular_store.has_tables()` counts rows for a notebook with **no `db_path IS NULL` filter**. So
in an ex-cursor notebook every aggregate-ish question still routes to the structured engine,
which builds a text-to-SQL prompt from the phantom external schema, spends a full model call on
it, then fails with `no such table` against the internal `tabular.db` and falls back to vector
RAG. No crash and no data loss — just several silent wasted seconds and a confusing
`[tabular] structured path empty/failed` line, permanently, on every such question.

Idempotent, marker-guarded, and never raises: a migration failure must not stop the app booting.
Deliberately does NOT drop `_tabular_catalog.db_path` / `.kind` (the shared spreadsheet path reads
`kind`) or `notebooks.type` / `config_json` (`notebook_store` reads both unconditionally).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MARKER = "cursor_purge_v1"


def _already_done(conn) -> bool:
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT)"
        )
        row = conn.execute("SELECT 1 FROM _migrations WHERE name = ?", (MARKER,)).fetchone()
        return row is not None
    except Exception:
        # If we cannot even read the marker, do nothing rather than risk repeating writes.
        return True


def run() -> dict:
    """Purge cursor residue once. Returns a small summary; never raises."""
    summary = {"ran": False, "catalog_rows": 0, "notebooks": 0}
    try:
        from storage.database import Database

        conn = Database().get_connection()
        if _already_done(conn):
            return summary

        def _exists(table: str) -> bool:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is not None

        # 1 — the load-bearing one: kill the phantom structured routing.
        if _exists("_tabular_catalog"):
            cur = conn.execute("DELETE FROM _tabular_catalog WHERE source_id LIKE 'cursor:%'")
            summary["catalog_rows"] = cur.rowcount or 0

        # 2-3 — hygiene: cursor-only tables nothing reads any more.
        for t in ("_cursor_views", "_cursor_routing_catalog"):
            if _exists(t):
                conn.execute(f"DROP TABLE IF EXISTS {t}")

        # 4 — relationship rows for notebooks that were cursor-typed.
        if _exists("_tabular_relationships") and _exists("notebooks"):
            conn.execute(
                "DELETE FROM _tabular_relationships WHERE notebook_id IN "
                "(SELECT id FROM notebooks WHERE type = 'cursor')"
            )

        # 5 — the other load-bearing one: an ex-cursor notebook becomes a standard notebook, so
        # nothing can resurface it as cursor-typed. Its ingested .md sources are untouched and
        # stay chattable; only the external-folder wiring in `config_json` goes.
        if _exists("notebooks"):
            cur = conn.execute(
                "UPDATE notebooks SET type = 'standard', config_json = '{}' WHERE type = 'cursor'"
            )
            summary["notebooks"] = cur.rowcount or 0

        conn.execute(
            "INSERT OR REPLACE INTO _migrations (name, applied_at) VALUES (?, datetime('now'))",
            (MARKER,),
        )
        conn.commit()
        summary["ran"] = True
        if summary["catalog_rows"] or summary["notebooks"]:
            logger.info(
                f"[migrate] cursor purge: {summary['catalog_rows']} catalog row(s), "
                f"{summary['notebooks']} notebook(s) converted to standard"
            )
        return summary
    except Exception as e:
        logger.warning(f"[migrate] cursor purge skipped: {e}")
        return summary
