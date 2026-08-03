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


def _connect_path(path: Path, read_only: bool = False) -> sqlite3.Connection:
    """Open a connection to a SPECIFIC SQLite file. `read_only=True` opens it `mode=ro`
    + `PRAGMA query_only` so writes are impossible even if a SELECT is crafted oddly — the
    only safe way to touch an EXTERNAL, user-owned .db (a Cursor Style notebook's monthly
    drop). We never write an external db."""
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
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


def _external_column_metadata(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    """Per-column metadata for a real table — SAME shape as `_column_metadata` (sanitized,
    dtype, low_cardinality, values/samples) but built from PRAGMA + SELECT DISTINCT instead
    of pandas. Column identifiers keep their REAL names (the external db is authoritative)."""
    cols: List[Dict[str, Any]] = []
    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        name = row[1]
        is_numeric = _sqlite_affinity(row[2]) == "number"
        entry: Dict[str, Any] = {"original": name, "sanitized": name,
                                 "dtype": "number" if is_numeric else "text"}
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


def index_external_db(notebook_id: str, source_id: str, db_path: str) -> Dict[str, Any]:
    """Introspect an EXTERNAL read-only .db and register its tables in the catalog with
    `db_path` provenance (queries then hit the real file read-only). Replaces prior catalog
    rows for `source_id` (monthly-refresh safe). The external db is NEVER copied or written.
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
        tables = [r[0] for r in ext.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        if not tables:
            return {"ok": False, "error": "database has no tables"}
        for t in tables:
            cols = _external_column_metadata(ext, t)
            try:
                rc = ext.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except Exception:
                rc = 0
            catalog_rows.append((t, cols, rc))
            summaries.append({"table_name": t, "row_count": rc,
                              "columns": [c["sanitized"] for c in cols]})
    except Exception as e:
        return {"ok": False, "error": f"introspection failed: {type(e).__name__}: {e}"}
    finally:
        ext.close()

    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    conn = _connect()
    try:
        _ensure_catalog(conn)
        # External tables live in the .db, NOT in tabular.db — only clear catalog rows.
        conn.execute(f"DELETE FROM {_CATALOG} WHERE source_id = ?", (source_id,))
        for t, cols, rc in catalog_rows:
            conn.execute(
                f"INSERT INTO {_CATALOG} (notebook_id, source_id, filename, sheet_name, "
                f"table_name, columns_json, row_count, created_at, db_path) "
                f"VALUES (?,?,?,?,?,?,?,?,?)",
                (notebook_id, source_id, p.name, t, t, json.dumps(cols), rc, now, str(p)))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "tables": summaries, "db_path": str(p), "filename": p.name}


def get_external_db_path(notebook_id: str, source_ids: Optional[List[str]] = None) -> Optional[str]:
    """The external .db path backing a Cursor notebook (all its tables share one), or None
    for an ordinary spreadsheet-backed notebook."""
    for t in get_schema(notebook_id, source_ids):
        if t.get("db_path"):
            return t["db_path"]
    return None


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
            _cols = "source_id, filename, sheet_name, table_name, columns_json, row_count, db_path"
            if source_ids:
                q = (f"SELECT {_cols} FROM {_CATALOG} WHERE notebook_id=? "
                     f"AND source_id IN ({','.join('?'*len(source_ids))})")
                rows = conn.execute(q, (notebook_id, *source_ids)).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_cols} FROM {_CATALOG} WHERE notebook_id=?", (notebook_id,)
                ).fetchall()
            out = []
            for sid, filename, sheet, table, cols_json, rc, db_path in rows:
                out.append({
                    "source_id": sid, "filename": filename, "sheet_name": sheet,
                    "table_name": table, "columns": json.loads(cols_json), "row_count": rc,
                    "db_path": db_path,
                })
            return out
        finally:
            conn.close()
    except Exception as e:
        print(f"[tabular] get_schema error: {e}")
        return []


def execute_readonly(sql: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Execute a single validated SELECT read-only. Returns {ok, columns, rows, truncated}.

    SQL is validated by the caller (tabular_query.safe_sql); this is the last-line
    enforcement via a read-only connection + query_only pragma + row cap. When `db_path`
    is given (a Cursor Style notebook), runs against that EXTERNAL .db in place, read-only.
    """
    try:
        conn = _connect_path(Path(db_path), read_only=True) if db_path else _connect(read_only=True)
        try:
            cur = conn.execute(sql)
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
