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
# Governance goes into EVERY SQL prompt, so keep it tight for speed — AGENTS.md (read first,
# the canonical joins/filters/metric defs) gets priority; the rest is trimmed. Smaller prompt
# = faster gemma. The full docs stay RAG-searchable via the ingested md sources.
_PER_FILE_BUDGET = 2500
_TOTAL_GOVERNANCE_BUDGET = 4500
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


# ── Canonical view collection (Phase 3 — owner-authored, optional) ─────────────
# The data owner may declare canonical views that pre-join the schema's common relationships, so
# the model queries a simple view instead of writing joins. Sources (either/both):
#   • a `views.sql` file in the folder, and/or
#   • ```sql fenced blocks under a "## Canonical Views" (or "## Views") heading in the governance md.
# We accept only single `CREATE VIEW <name> AS SELECT/WITH …` statements (no writes, no multi-stmt).
_VIEW_NAME_RE = re.compile(
    r'CREATE\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?["\'\[]?([A-Za-z_][A-Za-z0-9_]*)["\'\]]?\s+AS\s+',
    re.IGNORECASE | re.DOTALL)
_VIEW_BODY_OK = re.compile(r'\bAS\s+(SELECT|WITH)\b', re.IGNORECASE | re.DOTALL)
_VIEW_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|pragma|replace|truncate|"
    r"vacuum|reindex|grant|revoke|create\s+table|create\s+trigger)\b", re.IGNORECASE)
_VIEWS_HEADING_RE = re.compile(r'^#{1,6}\s+.*\bviews?\b', re.IGNORECASE)
_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _parse_view_statements(sql_text: str) -> List[Dict[str, str]]:
    """Extract valid single `CREATE VIEW … AS SELECT/WITH …` statements from a SQL blob."""
    specs: List[Dict[str, str]] = []
    for stmt in sql_text.split(";"):
        s = stmt.strip()
        if not s:
            continue
        m = _VIEW_NAME_RE.search(s)
        if not m:
            continue
        # Body must be a SELECT/WITH and contain no write/DDL keywords (defense in depth — the
        # companion is app-owned + the external db is attached read-only, but we stay strict).
        if not _VIEW_BODY_OK.search(s):
            continue
        body = s[m.end():]
        if _VIEW_FORBIDDEN.search(body):
            continue
        specs.append({"name": m.group(1), "ddl": s, "description": ""})
    return specs


def _collect_view_specs(folder_path: str,
                        governance_files: Dict[str, Optional[str]]) -> List[Dict[str, str]]:
    """Gather owner-authored canonical views from `views.sql` + governance-md ```sql fences under a
    Views heading. De-duplicated by name (first wins). Returns [] when none — the common case."""
    folder = Path(folder_path)
    specs: List[Dict[str, str]] = []
    # 1) views.sql (case-insensitive)
    try:
        for cand in folder.iterdir():
            if cand.is_file() and cand.name.lower() == "views.sql":
                specs.extend(_parse_view_statements(
                    cand.read_text(encoding="utf-8", errors="ignore")))
                break
    except Exception as e:
        logger.debug(f"[data_notebook] views.sql read skipped: {e}")
    # 2) ```sql fences under a "…Views" heading in each governance md
    for fname in governance_files.values():
        if not fname:
            continue
        try:
            text = (folder / fname).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lines = text.splitlines()
        in_views = False
        buf: List[str] = []
        for ln in lines:
            if _VIEWS_HEADING_RE.match(ln.strip()):
                in_views = True
                buf.append(ln)
                continue
            if in_views and re.match(r'^#{1,6}\s+', ln.strip()) and not _VIEWS_HEADING_RE.match(ln.strip()):
                in_views = False  # next non-views heading ends the section
            if in_views:
                buf.append(ln)
        if buf:
            for fence in _SQL_FENCE_RE.findall("\n".join(buf)):
                specs.extend(_parse_view_statements(fence))
    # de-dup by name, first wins
    seen: set = set()
    out: List[Dict[str, str]] = []
    for s in specs:
        k = s["name"].lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


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

    tables = idx.get("tables", [])
    # Phase 3: (re)build the owner-authored canonical view layer. Inert when no views are declared
    # (no companion db → the FK-injected path runs unchanged). Never blocks/raises the connect.
    view_count = 0
    try:
        view_specs = _collect_view_specs(str(folder), governance_files)
        vres = tabular_store.store_views(notebook_id, str(db_file), view_specs)
        view_count = vres.get("views", 0)
        if view_count:
            logger.info(f"[data_notebook] {notebook_id}: built {view_count} canonical view(s)")
    except Exception as e:
        logger.warning(f"[data_notebook] companion view build skipped ({notebook_id}): {e}")

    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    config = {
        "folder_path": str(folder),
        "db_path": str(db_file),
        "db_filename": db_file.name,
        "tables": tables,
        "governance_files": {k: v for k, v in governance_files.items() if v},
        # Cache the assembled governance at connect time so per-query routing does NOT re-read the
        # folder's .md each time (fewer folder touches = the folder authorization is honored once,
        # and a momentarily-unreadable folder can't blank the operating rules mid-session). Refreshed
        # on Refresh — the same cadence the monthly .md updates arrive.
        "governance_cache": _read_governance(str(folder), governance_files),
        "md_source_ids": {},   # filled by the background post-connect ingest
        "schema_fingerprint": _schema_fingerprint(tables),
        "connected_at": now,
        "refreshed_at": now,
    }
    await notebook_store.update(notebook_id, {"type": "cursor", "config": config})

    # Don't block the connect on the slow bits: ingest the .md for RAG + warm the SQL model
    # (so the first query isn't a ~10s cold reload). Fire-and-forget.
    import asyncio
    try:
        asyncio.create_task(_post_connect(notebook_id, str(folder), governance_files))
    except RuntimeError:
        pass  # no running loop (e.g. a sync caller) — skip the async warmup/ingest

    return {
        "ok": True, "db_filename": db_file.name, "tables": tables,
        "governance_files": [v for v in governance_files.values() if v],
        "table_count": len(tables),
        "row_total": sum(t.get("row_count", 0) for t in tables),
    }


async def _post_connect(notebook_id: str, folder_path: str,
                        governance_files: Dict[str, Optional[str]]) -> None:
    """Background work after a connect returns: ingest the .md as RAG sources (for doc
    questions) and warm the SQL model so the first query is fast. Never raises."""
    try:
        md_ids = await _ingest_md_sources(notebook_id, Path(folder_path), governance_files)
        if md_ids:
            from storage.notebook_store import notebook_store
            nb = await notebook_store.get(notebook_id)
            if nb and nb.get("type") == "cursor":
                cfg = dict(nb.get("config") or {})
                cfg["md_source_ids"] = md_ids
                await notebook_store.update(notebook_id, {"config": cfg})
    except Exception as e:
        logger.warning(f"[data_notebook] post-connect md ingest failed ({notebook_id}): {e}")
    try:
        from services import model_warmup
        from config import settings
        await model_warmup.warm_ollama_model(settings.ollama_model, keep_alive="30m")
    except Exception as e:
        logger.debug(f"[data_notebook] SQL-model warmup skipped: {e}")


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

    # Phase 3: rebuild the canonical view layer against the new-month .db (inert if none authored).
    try:
        view_specs = _collect_view_specs(str(folder), _locate_md(folder))
        tabular_store.store_views(notebook_id, str(db_file), view_specs)
    except Exception as e:
        logger.warning(f"[data_notebook] companion view rebuild skipped ({notebook_id}): {e}")

    refreshed_md = _locate_md(folder)
    config = dict(config)
    config.update({
        "db_path": str(db_file), "db_filename": db_file.name, "tables": tables,
        "governance_files": {k: v for k, v in refreshed_md.items() if v},
        "governance_cache": _read_governance(str(folder), refreshed_md),  # re-cache the fresh rules
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
        # Prefer the governance cached at connect/refresh (no per-query folder read → the folder
        # authorization is used once, not on every question). Fall back to disk only for notebooks
        # connected before caching existed, so their rules still load.
        governance = config.get("governance_cache")
        if governance is None:
            governance = _read_governance(config.get("folder_path", ""),
                                          config.get("governance_files", {}))
        schema = tabular_store.get_schema(notebook_id)
        logger.info(f"[cursor] nb={notebook_id} → text-to-SQL route: db={Path(db_path).name}, "
                    f"{len(schema)} tables, governance={len(governance)} chars")
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
            from services import cursor_sql
            results: List[str] = []
            for q in questions[:_BRIEFING_QUERY_CAP]:
                try:
                    res = await cursor_sql.answer(
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
            try:
                conn.execute(f"DELETE FROM {tabular_store._RELATIONSHIPS} WHERE notebook_id = ?",
                             (notebook_id,))
            except Exception:
                pass  # relationships table may not exist yet
            conn.commit()
        finally:
            conn.close()
        # Remove the notebook's stored canonical views too (never touches the external .db/folder).
        if hasattr(tabular_store, "drop_views"):
            tabular_store.drop_views(notebook_id)
    except Exception as e:
        logger.debug(f"[data_notebook] cleanup failed ({notebook_id}): {e}")
