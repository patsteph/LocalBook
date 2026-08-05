"""
Tabular Structured Store — typed SQLite tables for spreadsheet/CSV sources.

Why this exists
---------------
Vector RAG cannot answer aggregate/count questions over spreadsheets: a 150-row sheet
becomes ~188 chunks and retrieval only ever returns the top ~5, so "how many accounts in
Dallas" (9 rows across 23 chunks) is mathematically unanswerable. This store keeps the
ORIGINAL typed data in SQLite alongside the vector chunks, so a deterministic
`SELECT COUNT(*) ... WHERE city='Dallas'` returns the exact number.

Design
------
- One `tabular.db` under `settings.data_dir` (same convention as `localbook.db`).
- One SQLite table per (source_id, sheet), created with `df.to_sql` (pandas infers types).
- A `_tabular_catalog` row per table with column metadata + distinct values for
  low-cardinality text columns (the accuracy lever for text-to-SQL value mapping).
- Read-only query execution (`mode=ro` + `PRAGMA query_only`) with a hard row cap.

This module is ADDITIVE and tabular-only. It never touches the vector path.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings

# Text columns with <= this many distinct values get their full value list embedded in the
# text-to-SQL schema prompt (so the LLM can map "Texas"->"TX", "located in Dallas"->'Dallas').
LOW_CARDINALITY_MAX = 60
# Hard cap on rows returned by any structured query (guards a runaway SELECT).
MAX_RESULT_ROWS = 2000
# Cap the stored/prompted distinct-value strings so a wide column can't bloat the prompt.
_MAX_VALUE_LEN = 80

_CATALOG = "_tabular_catalog"
# Per-notebook FK/join graph for Cursor Style external DBs (declared + inferred). Cursor-only —
# the spreadsheet path never reads or writes this table.
_RELATIONSHIPS = "_tabular_relationships"


def _db_path() -> Path:
    return settings.data_dir / "tabular.db"


def _sanitize_ident(name: str, fallback: str) -> str:
    """Make a SQL-safe snake_case identifier from an arbitrary column/table name."""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower()).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"c_{s}"
    return s


def _table_name(source_id: str, sheet_idx: int) -> str:
    return f"tab_{_sanitize_ident(source_id, 'src')}_{sheet_idx}"


def _unique_columns(df) -> Dict[str, str]:
    """Return {original_col -> sanitized_col}, de-duplicating collisions."""
    mapping: Dict[str, str] = {}
    seen: set = set()
    for i, col in enumerate(df.columns):
        base = _sanitize_ident(col, f"col_{i+1}")
        cand = base
        n = 1
        while cand in seen:
            n += 1
            cand = f"{base}_{n}"
        seen.add(cand)
        mapping[col] = cand
    return mapping


def _connect_path(path: Path, read_only: bool = False,
                  query_only: bool = True) -> sqlite3.Connection:
    """Open a connection to a SPECIFIC SQLite file. `read_only=True` opens it `mode=ro`
    + `PRAGMA query_only` so writes are impossible even if a SELECT is crafted oddly — the
    only safe way to touch an EXTERNAL, user-owned .db (a Cursor Style notebook's monthly
    drop). We never write an external db. `query_only=False` (still `mode=ro`) is used ONLY to
    create TEMP views (which write the connection's temp schema, not the file) before the caller
    re-locks with `PRAGMA query_only = ON` for the actual query."""
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        if query_only:
            conn.execute("PRAGMA query_only = ON;")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
    return conn


def _connect(read_only: bool = False) -> sqlite3.Connection:
    """Open the app-owned shared `tabular.db` (spreadsheet/CSV tables)."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return _connect_path(path, read_only=read_only)


_MIGRATED = False


def _ensure_migrated() -> None:
    """Run the idempotent catalog migration (adds the `db_path` column) via a WRITE connect,
    once. The read paths (get_schema) now SELECT `db_path`; on an existing user's tabular.db
    that column won't exist until a write happens, so we force the ALTER before any read to
    avoid a regression on spreadsheet notebooks. Never raises."""
    global _MIGRATED
    if _MIGRATED:
        return
    try:
        if _db_path().exists():
            conn = _connect()
            try:
                _ensure_catalog(conn)
                conn.commit()
            finally:
                conn.close()
        _MIGRATED = True
    except Exception as e:
        print(f"[tabular] migration check failed (non-fatal): {e}")


def _ensure_catalog(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_CATALOG} (
            notebook_id TEXT NOT NULL,
            source_id   TEXT NOT NULL,
            filename    TEXT,
            sheet_name  TEXT NOT NULL,
            table_name  TEXT NOT NULL,
            columns_json TEXT NOT NULL,
            row_count   INTEGER NOT NULL,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (source_id, sheet_name)
        )"""
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_cat_nb ON {_CATALOG}(notebook_id)")
    # Migration-safe: `db_path` marks a catalog row whose table lives in an EXTERNAL,
    # read-in-place .db (a Cursor Style notebook). NULL = the app-owned shared tabular.db.
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({_CATALOG})").fetchall()}
    if "db_path" not in cols:
        conn.execute(f"ALTER TABLE {_CATALOG} ADD COLUMN db_path TEXT")
    # `kind` = 'table' | 'view'. Cursor Style external DBs ship purpose-built v_ VIEWS that pre-join
    # the base tables — surfacing them (and preferring them) is the biggest routing-accuracy lever.
    if "kind" not in cols:
        conn.execute(f"ALTER TABLE {_CATALOG} ADD COLUMN kind TEXT DEFAULT 'table'")


def _column_metadata(df, colmap: Dict[str, str]) -> List[Dict[str, Any]]:
    """Per-column metadata incl. distinct values for low-cardinality text columns."""
    import pandas as pd  # local import (lazy dep rule)

    cols: List[Dict[str, Any]] = []
    for original, sanitized in colmap.items():
        series = df[original]
        dtype = str(series.dtype)
        is_numeric = pd.api.types.is_numeric_dtype(series)
        entry: Dict[str, Any] = {
            "original": str(original),
            "sanitized": sanitized,
            "dtype": "number" if is_numeric else "text",
            "pandas_dtype": dtype,
        }
        non_null = series.dropna()
        if not is_numeric:
            distinct = non_null.astype(str).map(lambda v: v.strip()[:_MAX_VALUE_LEN])
            distinct = distinct[distinct != ""]
            uniq = sorted(distinct.unique().tolist())
            if len(uniq) <= LOW_CARDINALITY_MAX:
                entry["low_cardinality"] = True
                entry["values"] = uniq
            else:
                entry["low_cardinality"] = False
                entry["samples"] = uniq[:5]
        else:
            entry["low_cardinality"] = False
            entry["samples"] = [str(v) for v in non_null.head(3).tolist()]
        cols.append(entry)
    return cols


def index_source(
    notebook_id: str,
    source_id: str,
    filename: str,
    content: bytes,
    file_type: str,
) -> Dict[str, Any]:
    """Load a tabular source's sheets into typed SQLite tables + catalog.

    Re-parses the original bytes (the vector path already destroyed the structure).
    Returns a summary dict. NEVER raises into the caller — logs + returns {ok: False}.
    """
    import io

    import pandas as pd

    ft = (file_type or "").lower().lstrip(".")
    try:
        print(f"[tabular] indexing {source_id} '{filename}' (type={ft})")
        if ft in ("xlsx", "xls"):
            engine = "openpyxl" if ft == "xlsx" else "xlrd"
            df_dict = pd.read_excel(io.BytesIO(content), sheet_name=None, engine=engine)
        elif ft == "csv":
            df_dict = {"Sheet1": pd.read_csv(io.BytesIO(content))}
        elif ft == "ods":
            df_dict = pd.read_excel(io.BytesIO(content), sheet_name=None, engine="odf")
        else:
            print(f"[tabular] source {source_id} NOT indexed (unsupported type '{ft}')")
            return {"ok": False, "reason": f"unsupported type {ft}"}

        conn = _connect()
        try:
            _ensure_catalog(conn)
            # Idempotent re-index: drop prior tables + catalog rows for this source.
            _drop_source_tables(conn, source_id)

            sheets_out: List[Dict[str, Any]] = []
            for idx, (sheet_name, df) in enumerate(df_dict.items()):
                if df is None or df.shape[0] == 0 or df.shape[1] == 0:
                    continue
                # Drop fully-empty columns (common in exported sheets), keep everything else.
                df = df.dropna(axis=1, how="all")
                if df.shape[1] == 0:
                    continue
                colmap = _unique_columns(df)
                cols_meta = _column_metadata(df, colmap)
                table = _table_name(source_id, idx)
                df.rename(columns=colmap).to_sql(table, conn, if_exists="replace", index=False)

                low_card = [c["sanitized"] for c in cols_meta if c.get("low_cardinality")]
                conn.execute(
                    f"INSERT OR REPLACE INTO {_CATALOG} "
                    f"(notebook_id, source_id, filename, sheet_name, table_name, columns_json, row_count, created_at) "
                    f"VALUES (?,?,?,?,?,?,?,?)",
                    (
                        notebook_id, source_id, filename, str(sheet_name), table,
                        json.dumps(cols_meta), int(df.shape[0]),
                        time.strftime("%Y-%m-%dT%H:%M:%S"),
                    ),
                )
                sheets_out.append({"sheet": str(sheet_name), "table": table,
                                   "rows": int(df.shape[0]), "cols": int(df.shape[1])})
                print(f"[tabular] indexed '{filename}': sheet '{sheet_name}' -> {table} "
                      f"(rows={df.shape[0]}, cols={df.shape[1]}, low_card_cols={low_card})")
            conn.commit()
        finally:
            conn.close()

        if not sheets_out:
            return {"ok": False, "reason": "no non-empty sheets"}
        return {"ok": True, "sheets": sheets_out}
    except Exception as e:
        print(f"[tabular] index FAILED for {source_id} '{filename}': {type(e).__name__}: {e}")
        return {"ok": False, "reason": str(e)}


# ── External .db support (Cursor Style notebooks) ──────────────────────────────
# An external, user-owned .db is read IN PLACE (mode=ro) — never copied, never written.
# We introspect its real schema via PRAGMA/sqlite_master and store only METADATA in the
# catalog (with `db_path` provenance); execution hits the real file read-only.

def _sqlite_affinity(decl_type: str) -> str:
    """SQLite type-affinity → our two dtypes ('number' | 'text'). Rules per SQLite docs."""
    t = (decl_type or "").upper()
    if "INT" in t:
        return "number"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DEC", "NUM")):
        return "number"
    return "text"  # BLOB / unknown → treat as text for prompting


def _external_column_metadata(conn: sqlite3.Connection, table: str,
                              is_view: bool = False) -> List[Dict[str, Any]]:
    """Per-column metadata for a real table/view — SAME shape as `_column_metadata` (sanitized,
    dtype, low_cardinality, values/samples) but built from PRAGMA + SELECT DISTINCT instead
    of pandas. Column identifiers keep their REAL names (the external db is authoritative).

    For a VIEW we take ONLY the column names + types (no DISTINCT/COUNT scans) — a v_ view can be an
    8-table join, so per-column value profiling would make connect crawl. Views are for routing, not
    value grounding (that happens on the base tables)."""
    cols: List[Dict[str, Any]] = []
    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        name = row[1]
        is_numeric = _sqlite_affinity(row[2]) == "number"
        entry: Dict[str, Any] = {"original": name, "sanitized": name,
                                 "dtype": "number" if is_numeric else "text"}
        if is_view:
            entry["low_cardinality"] = False
            cols.append(entry)
            continue
        try:
            if not is_numeric:
                distinct = conn.execute(
                    f'SELECT COUNT(DISTINCT "{name}") FROM "{table}"').fetchone()[0]
                if distinct is not None and distinct <= LOW_CARDINALITY_MAX:
                    vals = [str(r[0]).strip()[:_MAX_VALUE_LEN] for r in conn.execute(
                        f'SELECT DISTINCT "{name}" FROM "{table}" WHERE "{name}" IS NOT NULL '
                        f'ORDER BY "{name}" LIMIT {LOW_CARDINALITY_MAX + 1}').fetchall()]
                    entry["low_cardinality"] = True
                    entry["values"] = [v for v in vals if v]
                else:
                    entry["low_cardinality"] = False
                    entry["samples"] = [str(r[0]) for r in conn.execute(
                        f'SELECT DISTINCT "{name}" FROM "{table}" WHERE "{name}" IS NOT NULL '
                        f'LIMIT 5').fetchall()]
            else:
                entry["low_cardinality"] = False
                entry["samples"] = [str(r[0]) for r in conn.execute(
                    f'SELECT "{name}" FROM "{table}" WHERE "{name}" IS NOT NULL LIMIT 3').fetchall()]
        except Exception:
            entry.setdefault("low_cardinality", False)
        cols.append(entry)
    return cols


# ── FK / join-graph inference (Cursor Style, Phase 2) ──────────────────────────
# Small models omit the FK columns needed to JOIN; handing them the exact join paths is the
# single biggest lever for join accuracy. We derive the graph ONCE at connect time from (a) the
# DB's declared foreign keys and (b) a conservative name-based heuristic for the (common) case of
# an enterprise .db that ships without declared FKs. Inference is deliberately cautious — a wrong
# join path is worse than a missing one, so we only link on id-like key columns.

# Join-key column suffixes — id-like AND name-like. Enterprise exports commonly ship WITHOUT
# declared FKs and join on a shared natural key like `record_key` / `account_name`, so we treat a
# trailing `_name` as a candidate key too (person/attribute *_name columns are denied below).
_KEY_SUFFIX = re.compile(r"(_id|_key|_code|_no|_num|_ref|_fk|_name)$", re.IGNORECASE)
# id-like only (used by the '<base>_id → <base> table' heuristic, which shouldn't fire on names).
_ID_SUFFIX = re.compile(r"(_id|_key|_code|_ref|_fk)$", re.IGNORECASE)
# Columns that look plausible but must never seed a join (too generic / not identity keys).
_KEY_DENY = {
    "id", "name", "date", "status", "type", "month", "year", "region", "state", "city",
    "amount", "value", "count", "total", "description", "notes", "note", "created_at",
    "updated_at", "quarter", "period", "email", "phone", "address", "title", "category",
}
# `*_name` columns that are ATTRIBUTES / person names, not join keys — denied even though they
# match the _name suffix (a shared rep_name is a coincidence, not a relationship).
_NAME_KEY_DENY = {
    "first_name", "last_name", "full_name", "middle_name", "display_name", "file_name",
    "user_name", "rep_name", "manager_name", "agent_name", "seller_name", "contact_name",
    "owner_name", "employee_name", "person_name", "sales_rep_name", "field_name", "column_name",
}


def _is_key_col(col: str) -> bool:
    """True if `col` looks like a join key (id-like OR name-like suffix, with a real base) and
    isn't a generic/attribute/person name. Bare 'id'/'name' are denied (they'd link everything)."""
    cl = str(col).strip().lower()
    if cl in _KEY_DENY or cl in _NAME_KEY_DENY:
        return False
    if not _KEY_SUFFIX.search(cl):
        return False
    return bool(_KEY_SUFFIX.sub("", cl))  # must have a base before the suffix (not just "_id")


def _match_table(base: str, tables: List[str]) -> Optional[str]:
    """Resolve a singular/plural base name (from '<base>_id') to an actual table name."""
    bl = base.strip().lower()
    if not bl:
        return None
    cands = {bl, bl + "s", bl + "es", bl.rstrip("s"), bl[:-1] if bl.endswith("y") else bl}
    for t in tables:
        if t.lower() in cands:
            return t
    return None


def _home_table(col: str, holders: List[str], table_cols: Dict[str, List[str]],
                table_pks: Dict[str, List[str]]) -> str:
    """Pick the 'home' table a shared key belongs to, so a hub key present in MANY tables becomes a
    star (home ← every other holder) instead of a clique or being dropped. Priority: (1) a holder
    where the column is a PK; (2) a holder named after the key's base (record_key → node/nodes);
    (3) the smallest holder (a dimension/lookup table usually owns the key)."""
    cl = col.lower()
    for t in holders:
        if cl in {p.lower() for p in table_pks.get(t, [])}:
            return t
    base = _KEY_SUFFIX.sub("", cl)
    if base:
        m = _match_table(base, holders)
        if m:
            return m
    return min(holders, key=lambda t: len(table_cols.get(t, [])))


def _infer_relationships(table_cols: Dict[str, List[str]], table_pks: Dict[str, List[str]],
                         declared: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Combine declared FKs with conservative name-based inference. Returns a de-duplicated list
    of {from_table, from_col, to_table, to_col, kind: 'declared'|'inferred'}."""
    rels: List[Dict[str, str]] = list(declared)
    have = {tuple(sorted([f'{r["from_table"]}.{r["from_col"]}', f'{r["to_table"]}.{r["to_col"]}']))
            for r in rels}
    tables = list(table_cols.keys())

    def _add(ft, fc, tt, tc, kind):
        if not (ft and fc and tt and tc) or ft == tt:
            return
        key = tuple(sorted([f"{ft}.{fc}", f"{tt}.{tc}"]))
        if key in have:
            return
        have.add(key)
        rels.append({"from_table": ft, "from_col": fc, "to_table": tt, "to_col": tc, "kind": kind})

    # Heuristic A — the SAME join-key column (id-like OR name-like) present in ≥2 tables, e.g.
    # record_roster.owner_id ↔ person_roster.owner_id, or the record_assignments.record_key ↔
    # record_roster.record_key hub key. A hub key in many tables → STAR to its home table (scales;
    # no arbitrary table-count cap that would drop the real hub key).
    keycols: Dict[str, List[str]] = {}
    for t, cols in table_cols.items():
        for c in cols:
            if _is_key_col(c):
                keycols.setdefault(c.lower(), []).append(t)
    for col, holders in keycols.items():
        holders = sorted(set(holders))
        if len(holders) < 2:
            continue
        home = _home_table(col, holders, table_cols, table_pks)
        real_home = next((c for c in table_cols[home] if c.lower() == col), col)
        for t in holders:
            if t == home:
                continue
            real_t = next((c for c in table_cols[t] if c.lower() == col), col)
            _add(t, real_t, home, real_home, "inferred")

    # Heuristic B — a '<base>_id' column pointing at a table named <base> (singular/plural). id-like
    # only — a '<base>_name' should link via the shared-key heuristic, not to a like-named table.
    for t, cols in table_cols.items():
        for c in cols:
            m = re.match(r"^(.*?)(_id|_key|_code|_ref|_fk)$", c, re.IGNORECASE)
            if not m or not m.group(1):
                continue
            target = _match_table(m.group(1), tables)
            if not target or target == t:
                continue
            # Target column: same name if present, else the target's PK, else its own '<base>_id'.
            tcols_lower = {x.lower(): x for x in table_cols[target]}
            to_col = (tcols_lower.get(c.lower())
                      or (table_pks.get(target) or [None])[0]
                      or tcols_lower.get("id"))
            if to_col:
                _add(t, c, target, to_col, "inferred")
    return rels


def _ensure_relationships(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_RELATIONSHIPS} (
            notebook_id TEXT PRIMARY KEY,
            relationships_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )


def get_relationships(notebook_id: str) -> List[Dict[str, str]]:
    """Return the stored FK/join graph for a Cursor notebook (declared + inferred), or []."""
    try:
        if not _db_path().exists():
            return []
        conn = _connect(read_only=True)
        try:
            row = conn.execute(
                f"SELECT relationships_json FROM {_RELATIONSHIPS} WHERE notebook_id = ?",
                (notebook_id,)).fetchone()
            return json.loads(row[0]) if row and row[0] else []
        finally:
            conn.close()
    except Exception:
        return []


def index_external_db(notebook_id: str, source_id: str, db_path: str) -> Dict[str, Any]:
    """Introspect an EXTERNAL read-only .db and register its tables in the catalog with
    `db_path` provenance (queries then hit the real file read-only). Replaces prior catalog
    rows for `source_id` (monthly-refresh safe). The external db is NEVER copied or written.
    Also derives the FK/join graph (declared + inferred) for the SQL prompt.
    Returns {ok, tables:[{table_name,row_count,columns}], db_path, filename} or {ok:False,error}."""
    p = Path(db_path)
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"database file not found: {db_path}"}
    try:
        ext = _connect_path(p, read_only=True)
    except Exception as e:
        return {"ok": False, "error": f"cannot open database: {type(e).__name__}: {e}"}
    catalog_rows: List[tuple] = []
    summaries: List[Dict[str, Any]] = []
    try:
        objects = [(r[0], r[1]) for r in ext.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        if not objects:
            return {"ok": False, "error": "database has no tables or views"}
        table_cols: Dict[str, List[str]] = {}
        table_pks: Dict[str, List[str]] = {}
        declared: List[Dict[str, str]] = []
        for name, otype in objects:
            is_view = (otype == "view")
            kind = "view" if is_view else "table"
            cols = _external_column_metadata(ext, name, is_view=is_view)
            rc = None  # views: skip COUNT (can be an 8-table join) — routing doesn't need it
            if not is_view:
                try:
                    rc = ext.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except Exception:
                    rc = 0
            catalog_rows.append((name, cols, rc, kind))
            summaries.append({"table_name": name, "kind": kind, "row_count": rc,
                              "columns": [c["sanitized"] for c in cols]})
            # FK/join inference is derived from BASE TABLES only (views are query targets that
            # already encapsulate joins — they aren't nodes in the base join graph).
            if not is_view:
                try:
                    info = ext.execute(f'PRAGMA table_info("{name}")').fetchall()
                    table_cols[name] = [r[1] for r in info]
                    table_pks[name] = [r[1] for r in info if r[5]]
                    for fk in ext.execute(f'PRAGMA foreign_key_list("{name}")').fetchall():
                        # PRAGMA foreign_key_list: (id, seq, table, from, to, on_update, on_delete, match)
                        to_tbl, from_col, to_col = fk[2], fk[3], fk[4]
                        if not to_col:  # FK to the target's PK when 'to' is unspecified
                            to_col = (table_pks.get(to_tbl) or ["rowid"])[0]
                        declared.append({"from_table": name, "from_col": from_col,
                                         "to_table": to_tbl, "to_col": to_col, "kind": "declared"})
                except Exception:
                    table_cols.setdefault(name, [c["sanitized"] for c in cols])
                    table_pks.setdefault(name, [])
        relationships = _infer_relationships(table_cols, table_pks, declared)
    except Exception as e:
        return {"ok": False, "error": f"introspection failed: {type(e).__name__}: {e}"}
    finally:
        ext.close()

    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    conn = _connect()
    try:
        _ensure_catalog(conn)
        _ensure_relationships(conn)
        # External tables live in the .db, NOT in tabular.db — only clear catalog rows.
        conn.execute(f"DELETE FROM {_CATALOG} WHERE source_id = ?", (source_id,))
        for name, cols, rc, kind in catalog_rows:
            conn.execute(
                f"INSERT INTO {_CATALOG} (notebook_id, source_id, filename, sheet_name, "
                f"table_name, columns_json, row_count, created_at, db_path, kind) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?)",
                (notebook_id, source_id, p.name, name, name, json.dumps(cols),
                 (rc if rc is not None else 0), now, str(p), kind))
        # Persist the FK/join graph (monthly-refresh safe: replace the notebook's row).
        conn.execute(
            f"INSERT OR REPLACE INTO {_RELATIONSHIPS} (notebook_id, relationships_json, created_at) "
            f"VALUES (?,?,?)", (notebook_id, json.dumps(relationships), now))
        conn.commit()
    finally:
        conn.close()
    n_views = sum(1 for s in summaries if s.get("kind") == "view")
    return {"ok": True, "tables": summaries, "db_path": str(p), "filename": p.name,
            "relationships": len(relationships),
            "table_count": len(summaries) - n_views, "view_count": n_views}


def get_external_db_path(notebook_id: str, source_ids: Optional[List[str]] = None) -> Optional[str]:
    """The external .db path backing a Cursor notebook (all its tables share one), or None
    for an ordinary spreadsheet-backed notebook."""
    for t in get_schema(notebook_id, source_ids):
        if t.get("db_path"):
            return t["db_path"]
    return None


# ── Canonical view-layer (Cursor Style, Phase 3) ──────────────────────────────
# The strongest structural accuracy lever is pre-built VIEWs that hide joins — but auto-guessing
# views is curation-dependent (per the research), so we do NOT invent them. The data owner authors
# canonical views (a `views.sql`, or ```sql fences under a "## Canonical Views" heading in
# AGENTS.md). We store each as a `CREATE TEMP VIEW` and materialize them in the read-only external
# connection AT QUERY TIME — TEMP views live in the connection's temp schema, so they can reference
# the read-only main db's real tables directly (the external file is NEVER written). Inert when no
# views are authored (no temp views created → the normal FK-injected path runs, zero added latency).

_CURSOR_VIEWS = "_cursor_views"


def _ensure_cursor_views(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_CURSOR_VIEWS} (
            notebook_id TEXT NOT NULL,
            name        TEXT NOT NULL,
            ddl         TEXT NOT NULL,
            description TEXT,
            columns_json TEXT,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (notebook_id, name)
        )"""
    )


def _as_temp_view_ddl(ddl: str) -> str:
    """Rewrite `CREATE VIEW …` → `CREATE TEMP VIEW …` (idempotent)."""
    return re.sub(r"^\s*CREATE\s+(TEMP\s+|TEMPORARY\s+)?VIEW",
                  "CREATE TEMP VIEW", ddl, count=1, flags=re.IGNORECASE)


def get_views(notebook_id: str) -> List[Dict[str, Any]]:
    """Authored canonical views for a Cursor notebook: [{name, description, columns}], or []."""
    try:
        if not _db_path().exists():
            return []
        conn = _connect(read_only=True)
        try:
            rows = conn.execute(
                f"SELECT name, description, columns_json FROM {_CURSOR_VIEWS} "
                f"WHERE notebook_id = ?", (notebook_id,)).fetchall()
            return [{"name": r[0], "description": r[1] or "",
                     "columns": json.loads(r[2]) if r[2] else []} for r in rows]
        except sqlite3.OperationalError:
            return []  # table not created yet
        finally:
            conn.close()
    except Exception:
        return []


def get_view_ddls(notebook_id: str) -> List[str]:
    """The `CREATE TEMP VIEW …` statements to materialize before a query, or []."""
    try:
        if not _db_path().exists():
            return []
        conn = _connect(read_only=True)
        try:
            rows = conn.execute(
                f"SELECT ddl FROM {_CURSOR_VIEWS} WHERE notebook_id = ?", (notebook_id,)).fetchall()
            return [r[0] for r in rows if r[0]]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
    except Exception:
        return []


def drop_views(notebook_id: str) -> None:
    """Remove a notebook's stored canonical views (refresh/delete or none authored)."""
    try:
        if not _db_path().exists():
            return
        conn = _connect()
        try:
            _ensure_cursor_views(conn)
            conn.execute(f"DELETE FROM {_CURSOR_VIEWS} WHERE notebook_id = ?", (notebook_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def store_views(notebook_id: str, db_path: str,
                view_specs: List[Dict[str, str]]) -> Dict[str, Any]:
    """Validate + store owner-authored canonical views (`view_specs` = [{name, ddl, description}]).
    Each is validated by materializing it as a TEMP VIEW in the read-only external connection and
    reading its columns. No valid views → clears + returns {ok, views: 0}. Never raises."""
    drop_views(notebook_id)
    if not view_specs:
        return {"ok": True, "views": 0}
    validated: List[tuple] = []
    try:
        # query_only=False so TEMP views can be created (temp schema only); mode=ro still guards
        # the external file — this connection can never write it.
        ext = _connect_path(Path(db_path), read_only=True, query_only=False)
    except Exception as e:
        return {"ok": False, "error": f"cannot open database: {e}", "views": 0}
    try:
        for spec in view_specs:
            name, ddl = spec.get("name"), spec.get("ddl")
            if not name or not ddl:
                continue
            temp_ddl = _as_temp_view_ddl(ddl)
            try:
                ext.execute(f'DROP VIEW IF EXISTS "{name}"')
                ext.execute(temp_ddl)
                cur = ext.execute(f'SELECT * FROM "{name}" LIMIT 0')
                cols = [d[0] for d in cur.description] if cur.description else []
                validated.append((name, temp_ddl, spec.get("description", ""), json.dumps(cols)))
            except Exception as e:
                print(f"[cursor] canonical view '{name}' skipped ({type(e).__name__}: {e})")
    finally:
        ext.close()
    if not validated:
        return {"ok": True, "views": 0}
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    conn = _connect()
    try:
        _ensure_cursor_views(conn)
        conn.execute(f"DELETE FROM {_CURSOR_VIEWS} WHERE notebook_id = ?", (notebook_id,))
        for name, temp_ddl, desc, cols_json in validated:
            conn.execute(
                f"INSERT OR REPLACE INTO {_CURSOR_VIEWS} "
                f"(notebook_id, name, ddl, description, columns_json, created_at) VALUES (?,?,?,?,?,?)",
                (notebook_id, name, temp_ddl, desc, cols_json, now))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "views": len(validated)}


def _drop_source_tables(conn: sqlite3.Connection, source_id: str) -> None:
    rows = conn.execute(
        f"SELECT table_name FROM {_CATALOG} WHERE source_id = ?", (source_id,)
    ).fetchall()
    for (table,) in rows:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f"DELETE FROM {_CATALOG} WHERE source_id = ?", (source_id,))


def delete_source(source_id: str) -> None:
    """Drop a source's tables + catalog rows (wire into notebook/source delete cascade)."""
    try:
        if not _db_path().exists():
            return
        conn = _connect()
        try:
            _ensure_catalog(conn)
            _drop_source_tables(conn, source_id)
            conn.commit()
        finally:
            conn.close()
        print(f"[tabular] deleted structured tables for source {source_id}")
    except Exception as e:
        print(f"[tabular] delete_source error for {source_id}: {e}")


def has_tables(notebook_id: str, source_ids: Optional[List[str]] = None) -> bool:
    """True if the notebook has ≥1 structured tabular table (routing guard — must be cheap)."""
    try:
        if not _db_path().exists():
            return False
        conn = _connect(read_only=True)
        try:
            if source_ids:
                q = f"SELECT 1 FROM {_CATALOG} WHERE notebook_id=? AND source_id IN ({','.join('?'*len(source_ids))}) LIMIT 1"
                row = conn.execute(q, (notebook_id, *source_ids)).fetchone()
            else:
                row = conn.execute(
                    f"SELECT 1 FROM {_CATALOG} WHERE notebook_id=? LIMIT 1", (notebook_id,)
                ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


def get_schema(notebook_id: str, source_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Return catalog entries (tables + column metadata) for building the text-to-SQL prompt."""
    try:
        if not _db_path().exists():
            return []
        _ensure_migrated()   # guarantee the db_path column exists before we SELECT it
        conn = _connect(read_only=True)
        try:
            _cols = ("source_id, filename, sheet_name, table_name, columns_json, row_count, "
                     "db_path, kind")
            if source_ids:
                q = (f"SELECT {_cols} FROM {_CATALOG} WHERE notebook_id=? "
                     f"AND source_id IN ({','.join('?'*len(source_ids))})")
                rows = conn.execute(q, (notebook_id, *source_ids)).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_cols} FROM {_CATALOG} WHERE notebook_id=?", (notebook_id,)
                ).fetchall()
            out = []
            for sid, filename, sheet, table, cols_json, rc, db_path, kind in rows:
                out.append({
                    "source_id": sid, "filename": filename, "sheet_name": sheet,
                    "table_name": table, "columns": json.loads(cols_json), "row_count": rc,
                    "db_path": db_path, "kind": kind or "table",
                })
            return out
        finally:
            conn.close()
    except Exception as e:
        print(f"[tabular] get_schema error: {e}")
        return []


def execute_readonly(sql: str, db_path: Optional[str] = None,
                     params: Optional[List[Any]] = None,
                     temp_views: Optional[List[str]] = None) -> Dict[str, Any]:
    """Execute a single validated SELECT read-only. Returns {ok, columns, rows, truncated}.

    SQL is validated by the caller (tabular_query.safe_sql); this is the last-line
    enforcement via a read-only connection + query_only pragma + row cap. When `db_path`
    is given (a Cursor Style notebook), runs against that EXTERNAL .db in place, read-only.
    `params` binds placeholders for internally-built parameterized lookups (never user SQL).
    `temp_views` = owner-authored `CREATE TEMP VIEW …` statements materialized in this connection's
    temp schema before the query (canonical views over the read-only db; the file is never written)."""
    try:
        # TEMP views must be created BEFORE query_only is locked (they write the temp schema, never
        # the mode=ro file). So for the cursor+views case, defer query_only, create views, re-lock.
        need_temp = bool(temp_views) and bool(db_path)
        if db_path:
            conn = _connect_path(Path(db_path), read_only=True, query_only=not need_temp)
        else:
            conn = _connect(read_only=True)
        try:
            if temp_views:
                for ddl in temp_views:
                    try:
                        conn.execute(ddl)
                    except Exception as e:
                        print(f"[cursor] temp view create skipped ({type(e).__name__}: {e})")
            if need_temp:
                conn.execute("PRAGMA query_only = ON;")  # re-lock for the actual (model) query
            cur = conn.execute(sql, params) if params is not None else conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(MAX_RESULT_ROWS + 1)
            truncated = len(rows) > MAX_RESULT_ROWS
            rows = rows[:MAX_RESULT_ROWS]
            return {"ok": True, "columns": columns,
                    "rows": [list(r) for r in rows], "truncated": truncated}
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
