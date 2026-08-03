"""Cursor Style notebook — connect a folder on disk (a monthly SQLite drop + its markdown
instruction files) and query it via governed, read-only text-to-SQL.

Model (matches how people use Cursor for this): a folder holds ONE `.db`/`.sqlite` file plus
`AGENTS.md` / `DATA_OVERVIEW.md` / `domain_guide.md` / `README.md`. The `.db` is read
IN PLACE, read-only (never copied, never written). The `AGENTS.md` + `DATA_OVERVIEW.md` +
`domain_guide.md` are AUTHORITATIVE operating rules injected into every SQL prompt
(canonical joins, required filters, metric definitions, example questions). `README.md` and
all the docs are also ingested as normal sources so "what does the README say" falls to RAG.

Monthly refresh = the folder's contents are replaced; `refresh()` re-introspects the `.db`
schema and re-reads the governance from disk. Governance is ALWAYS read fresh from disk, so
updated metric definitions take effect immediately without re-ingesting.

This module is the only NEW module for the feature — the SQL engine (`tabular_query`),
read-only execution + catalog (`tabular_store`), md ingestion (`document_processor`), and the
routing hook (`rag_engine`) are all reused.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# SQLite file magic — validate a candidate .db is really SQLite before opening it.
_SQLITE_MAGIC = b"SQLite format 3\x00"
_DB_EXTS = (".db", ".sqlite", ".sqlite3")
# Governance budgets (chars) so a huge doc can't blow the SQL prompt window. Full text stays
# RAG-searchable via the ingested md sources.
_PER_FILE_BUDGET = 4000
_TOTAL_GOVERNANCE_BUDGET = 9000
# Authoritative governance docs, in priority order (README is intentionally excluded — it's
# for the RAG fallback, not hard SQL rules).
_GOVERNANCE_ROLES = ("agents", "data_overview", "domain_guide")
# role -> the canonical filename we match case-insensitively.
_MD_ROLES = {
    "agents": "agents.md",
    "data_overview": "data_overview.md",
    "domain_guide": "domain_guide.md",
    "readme": "readme.md",
}


# ── Discovery helpers ──────────────────────────────────────────────────────────
def _validate_folder(folder_path: str) -> Path:
    p = Path(folder_path).expanduser()
    try:
        p = p.resolve()
    except Exception:
        raise ValueError(f"invalid folder path: {folder_path}")
    if not p.exists():
        raise ValueError(f"folder does not exist: {p}")
    if not p.is_dir():
        raise ValueError(f"not a folder: {p}")
    return p


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == _SQLITE_MAGIC
    except Exception:
        return False


def _locate_db(folder: Path) -> Path:
    """Find the single SQLite database in `folder`. Requires exactly one (v1)."""
    candidates = [
        c for c in sorted(folder.iterdir())
        if c.is_file() and c.suffix.lower() in _DB_EXTS and _is_sqlite(c)
    ]
    if not candidates:
        raise ValueError("no SQLite database (.db/.sqlite) found in the folder")
    if len(candidates) > 1:
        names = ", ".join(c.name for c in candidates)
        raise ValueError(f"multiple databases found ({names}); keep exactly one .db in the folder")
    return candidates[0]


def _locate_md(folder: Path) -> Dict[str, Optional[str]]:
    """Map each governance role → the actual filename present (case-insensitive), or None."""
    by_lower = {c.name.lower(): c.name for c in folder.iterdir()
                if c.is_file() and c.suffix.lower() == ".md"}
    return {role: by_lower.get(canonical) for role, canonical in _MD_ROLES.items()}


def _read_governance(folder_path: str, governance_files: Dict[str, Optional[str]]) -> str:
    """Assemble the AUTHORITATIVE governance text from disk (always fresh), budgeted so it
    can't overflow the SQL prompt. AGENTS.md → DATA_OVERVIEW.md → domain_guide.md."""
    folder = Path(folder_path)
    blocks: List[str] = []
    total = 0
    for role in _GOVERNANCE_ROLES:
        fname = governance_files.get(role)
        if not fname:
            continue
        try:
            text = (folder / fname).read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as e:
            logger.debug(f"[data_notebook] could not read {fname}: {e}")
            continue
        if not text:
            continue
        if len(text) > _PER_FILE_BUDGET:
            text = text[:_PER_FILE_BUDGET] + "\n…(truncated)"
        if total + len(text) > _TOTAL_GOVERNANCE_BUDGET:
            text = text[: max(0, _TOTAL_GOVERNANCE_BUDGET - total)]
        if not text:
            break
        blocks.append(f"### {fname}\n{text}")
        total += len(text)
        if total >= _TOTAL_GOVERNANCE_BUDGET:
            break
    return "\n\n".join(blocks)


def _schema_fingerprint(tables: List[Dict[str, Any]]) -> str:
    parts = []
    for t in sorted(tables, key=lambda x: x.get("table_name", "")):
        cols = ",".join(sorted(c for c in t.get("columns", [])))
        parts.append(f"{t.get('table_name')}({cols})")
    return hashlib.sha1("|".join(parts).encode("utf-8", "ignore")).hexdigest()


# ── md ingestion (best-effort — never blocks the connect) ──────────────────────
async def _ingest_md_sources(notebook_id: str, folder: Path,
                             governance_files: Dict[str, Optional[str]]) -> Dict[str, str]:
    """Ingest each present .md as a normal RAG source so doc questions ('what does the README
    say') work. Returns {role: source_id}. Best-effort: a failure on one file is logged, not raised."""
    from services.document_processor import document_processor
    out: Dict[str, str] = {}
    for role, fname in governance_files.items():
        if not fname:
            continue
        try:
            content = (folder / fname).read_bytes()
            src = await document_processor.process(content, fname, notebook_id)
            sid = (src or {}).get("id")
            if sid:
                out[role] = sid
        except Exception as e:
            logger.warning(f"[data_notebook] md ingest failed for {fname}: {e}")
    return out


# ── Public API ─────────────────────────────────────────────────────────────────
async def connect_folder(notebook_id: str, folder_path: str) -> Dict[str, Any]:
    """Connect a Cursor Style notebook to a folder: locate the .db + .md files, introspect the
    schema (read-only, in place), ingest the docs for RAG, read the governance, and persist the
    connection on the notebook. Never writes the external .db. Returns a status summary; on a
    validation failure returns {ok: False, error} rather than raising."""
    from storage.notebook_store import notebook_store
    from storage import tabular_store

    try:
        folder = _validate_folder(folder_path)
        db_file = _locate_db(folder)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    governance_files = _locate_md(folder)
    source_id = f"cursor:{notebook_id}"
    idx = tabular_store.index_external_db(notebook_id, source_id, str(db_file))
    if not idx.get("ok"):
        return {"ok": False, "error": idx.get("error", "could not read the database")}

    md_source_ids = await _ingest_md_sources(notebook_id, folder, governance_files)

    tables = idx.get("tables", [])
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    config = {
        "folder_path": str(folder),
        "db_path": str(db_file),
        "db_filename": db_file.name,
        "tables": tables,
        "governance_files": {k: v for k, v in governance_files.items() if v},
        "md_source_ids": md_source_ids,
        "schema_fingerprint": _schema_fingerprint(tables),
        "connected_at": now,
        "refreshed_at": now,
    }
    await notebook_store.update(notebook_id, {"type": "cursor", "config": config})

    return {
        "ok": True, "db_filename": db_file.name, "tables": tables,
        "governance_files": [v for v in governance_files.values() if v],
        "table_count": len(tables),
        "row_total": sum(t.get("row_count", 0) for t in tables),
    }


async def refresh(notebook_id: str) -> Dict[str, Any]:
    """Re-introspect the connected folder's .db + re-read governance (monthly refresh). Reports
    schema drift. Governance is always read fresh from disk, so this mainly refreshes the schema
    catalog + surfaces added/removed columns."""
    from storage.notebook_store import notebook_store
    from storage import tabular_store

    nb = await notebook_store.get(notebook_id)
    if not nb or nb.get("type") != "cursor":
        return {"ok": False, "error": "not a Cursor Style notebook"}
    config = nb.get("config") or {}
    folder_path = config.get("folder_path")
    if not folder_path:
        return {"ok": False, "error": "no connected folder — connect one first"}
    try:
        folder = _validate_folder(folder_path)
        db_file = _locate_db(folder)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    old_fp = config.get("schema_fingerprint", "")
    source_id = f"cursor:{notebook_id}"
    idx = tabular_store.index_external_db(notebook_id, source_id, str(db_file))
    if not idx.get("ok"):
        return {"ok": False, "error": idx.get("error", "could not read the database")}
    tables = idx.get("tables", [])
    new_fp = _schema_fingerprint(tables)

    config = dict(config)
    config.update({
        "db_path": str(db_file), "db_filename": db_file.name, "tables": tables,
        "governance_files": {k: v for k, v in _locate_md(folder).items() if v},
        "schema_fingerprint": new_fp,
        "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    })
    await notebook_store.update(notebook_id, {"config": config})
    return {
        "ok": True, "schema_changed": old_fp != new_fp, "db_filename": db_file.name,
        "tables": tables, "table_count": len(tables),
        "row_total": sum(t.get("row_count", 0) for t in tables),
    }


async def get_cursor_context(notebook_id: str) -> Optional[Dict[str, Any]]:
    """For a Cursor Style notebook, return {db_path, schema, governance, config}; else None.
    Called by the chat-routing hook and (later) Studio. Never raises → None on any problem."""
    try:
        from storage.notebook_store import notebook_store
        from storage import tabular_store

        nb = await notebook_store.get(notebook_id)
        if not nb or nb.get("type") != "cursor":
            return None
        config = nb.get("config") or {}
        db_path = config.get("db_path") or tabular_store.get_external_db_path(notebook_id)
        if not db_path:
            return None
        governance = _read_governance(config.get("folder_path", ""),
                                      config.get("governance_files", {}))
        schema = tabular_store.get_schema(notebook_id)
        return {"db_path": db_path, "schema": schema, "governance": governance, "config": config}
    except Exception as e:
        logger.warning(f"[data_notebook] get_cursor_context failed ({notebook_id}): {e}")
        return None


# ── Studio over live data (Phase 3) ────────────────────────────────────────────
_BRIEFING_QUERY_CAP = 6


def _extract_example_questions(folder_path: str, governance_files: Dict[str, Optional[str]],
                              limit: int = _BRIEFING_QUERY_CAP) -> List[str]:
    """Pull the data owner's OWN example questions from domain_guide.md / DATA_OVERVIEW.md
    (lines ending in '?'). These become the live briefing queries — the KPIs leaders actually
    ask — so a generated briefing reflects what the docs say matters."""
    folder = Path(folder_path)
    out: List[str] = []
    for role in ("domain_guide", "data_overview", "agents"):
        fname = governance_files.get(role)
        if not fname:
            continue
        try:
            text = (folder / fname).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            s = line.strip().lstrip("-*0123456789.) ").strip().strip('"\'').strip()
            if s.endswith("?") and 8 <= len(s) <= 160 and s not in out:
                out.append(s)
                if len(out) >= limit:
                    return out
    return out


async def build_data_context(notebook_id: str, topic: Optional[str] = None) -> str:
    """Assemble a Studio context for a Cursor Style notebook from LIVE data: the operating
    rules (governance), the schema, and the results of the doc's own example questions run
    read-only against the .db. This replaces vector-chunk context so any Studio output (a
    monthly briefing, a summary, a podcast) is grounded in real, current numbers. Never raises."""
    try:
        cur = await get_cursor_context(notebook_id)
        if not cur:
            return ""
        parts: List[str] = []
        if cur.get("governance"):
            parts.append("## Operating rules & metric definitions (authoritative)\n" + cur["governance"])

        schema = cur.get("schema") or []
        if schema:
            lines = ["## Database schema"]
            for t in schema:
                cols = ", ".join(c.get("sanitized", "") for c in t.get("columns", []))
                lines.append(f"- **{t.get('table_name')}** ({t.get('row_count', 0)} rows): {cols}")
            parts.append("\n".join(lines))

        config = cur.get("config") or {}
        questions = _extract_example_questions(config.get("folder_path", ""),
                                               config.get("governance_files", {}))
        if topic and topic.strip():
            questions = [topic.strip()] + [q for q in questions if q.lower() != topic.strip().lower()]
        if questions:
            from services import tabular_query
            results: List[str] = []
            for q in questions[:_BRIEFING_QUERY_CAP]:
                try:
                    res = await tabular_query.answer_tabular(
                        notebook_id, q, db_path=cur["db_path"], governance=cur.get("governance"))
                    if res.get("ok"):
                        results.append(f"### {q}\n_SQL: {res['sql']}_\n\n{res['answer']}")
                except Exception:
                    continue
            if results:
                parts.append("## Live figures (computed from the database just now)\n\n"
                             + "\n\n".join(results))
        return "\n\n".join(parts)
    except Exception as e:
        logger.warning(f"[data_notebook] build_data_context failed ({notebook_id}): {e}")
        return ""


def cleanup(notebook_id: str) -> None:
    """Drop a cursor notebook's external catalog rows (never touches the external .db/folder).
    Wire into notebook delete."""
    try:
        from storage import tabular_store
        conn = tabular_store._connect()
        try:
            conn.execute(f"DELETE FROM {tabular_store._CATALOG} WHERE source_id = ?",
                         (f"cursor:{notebook_id}",))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[data_notebook] cleanup failed ({notebook_id}): {e}")
