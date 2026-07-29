"""Canvas Layout Store — per-notebook spatial layout for the Journey Canvas (2.2.0, Crawl P1).

Persists WHERE a node sits (geometry) + the edges the user draws + the viewport — the
greenfield spatial layer. WHAT is inside a node stays in the Artifact/renderer layer;
this store never holds artifact payloads beyond a stable snapshot for offline render.

SQLite (shared localbook.db via storage.database.Database) rather than JSON: layout is
written at fine granularity (every node drag / edge draw / viewport pan is a small patch,
many per second), so row-level UPDATE beats a whole-file atomic rewrite. Mirrors the
activity_ledger pattern: lazy idempotent schema, thread-local connection, never raises.

Node model = hybrid (spec §"Node model"): a frozen `snapshot_json` (stable render) + a
live `ref_type`/`ref_id` link (open-source / re-sync). Edge `state` ∈ the five spec states;
Crawl persists only user-drawn edges (candidates are recomputed-transient, not stored).

Internal `_*(conn, ...)` helpers take an explicit connection so the SQL logic is unit-tested
against an in-memory sqlite with zero production writes; public functions acquire the shared
connection.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Edge states (keep in sync with the frontend + the spec's five-state table).
EDGE_STATES = ("candidate", "provenance", "user", "curator", "researched")

_SCHEMA_READY = False


# --------------------------------------------------------------------------
# Schema (idempotent — safe on every boot / first call).
# --------------------------------------------------------------------------
def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS canvas_nodes (
            id            TEXT PRIMARY KEY,
            notebook_id   TEXT NOT NULL,
            x             REAL NOT NULL,
            y             REAL NOT NULL,
            kind          TEXT NOT NULL,
            ref_type      TEXT,
            ref_id        TEXT,
            snapshot_json TEXT DEFAULT '{}',
            title         TEXT DEFAULT '',
            z             INTEGER DEFAULT 0,
            width         REAL,
            height        REAL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )
    # Migration-safe: existing prod DBs already have canvas_nodes without width/height
    # (added 2.2.0 resize-persistence). ADD COLUMN only when absent — nullable, no default.
    existing_cols = {r[1] for r in cur.execute("PRAGMA table_info(canvas_nodes)").fetchall()}
    if "width" not in existing_cols:
        cur.execute("ALTER TABLE canvas_nodes ADD COLUMN width REAL")
    if "height" not in existing_cols:
        cur.execute("ALTER TABLE canvas_nodes ADD COLUMN height REAL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canvas_nodes_nb ON canvas_nodes(notebook_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS canvas_edges (
            id          TEXT PRIMARY KEY,
            notebook_id TEXT NOT NULL,
            source      TEXT NOT NULL,
            target      TEXT NOT NULL,
            state       TEXT NOT NULL,
            label       TEXT DEFAULT '',
            meta_json   TEXT DEFAULT '{}',
            created_at  TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canvas_edges_nb ON canvas_edges(notebook_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS canvas_viewport (
            notebook_id TEXT PRIMARY KEY,
            x           REAL DEFAULT 0,
            y           REAL DEFAULT 0,
            zoom        REAL DEFAULT 1,
            updated_at  TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _now() -> str:
    return datetime.utcnow().isoformat()


def _as_float_or_none(v: Any) -> Optional[float]:
    """Coerce a width/height to float, preserving None (a node that was never resized)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Row <-> dict mappers
# --------------------------------------------------------------------------
def _node_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "x": row["x"],
        "y": row["y"],
        "kind": row["kind"],
        "ref_type": row["ref_type"],
        "ref_id": row["ref_id"],
        "snapshot": json.loads(row["snapshot_json"] or "{}"),
        "title": row["title"] or "",
        "z": row["z"] or 0,
        "width": row["width"],
        "height": row["height"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _edge_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source"],
        "target": row["target"],
        "state": row["state"],
        "label": row["label"] or "",
        "meta": json.loads(row["meta_json"] or "{}"),
        "created_at": row["created_at"],
    }


# --------------------------------------------------------------------------
# Connection-injectable core (unit-tested with an in-memory conn)
# --------------------------------------------------------------------------
def _get_layout(conn: sqlite3.Connection, notebook_id: str) -> Dict[str, Any]:
    nodes = [
        _node_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM canvas_nodes WHERE notebook_id = ? ORDER BY z, created_at",
            (notebook_id,),
        ).fetchall()
    ]
    edges = [
        _edge_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM canvas_edges WHERE notebook_id = ? ORDER BY created_at",
            (notebook_id,),
        ).fetchall()
    ]
    vp_row = conn.execute(
        "SELECT x, y, zoom FROM canvas_viewport WHERE notebook_id = ?", (notebook_id,)
    ).fetchone()
    viewport = (
        {"x": vp_row["x"], "y": vp_row["y"], "zoom": vp_row["zoom"]}
        if vp_row
        else {"x": 0.0, "y": 0.0, "zoom": 1.0}
    )
    return {"nodes": nodes, "edges": edges, "viewport": viewport}


def _save_layout(
    conn: sqlite3.Connection,
    notebook_id: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    viewport: Optional[Dict[str, Any]] = None,
) -> None:
    """Full replace of a notebook's nodes + edges (bulk autosave). Viewport upserted if given."""
    now = _now()
    conn.execute("DELETE FROM canvas_nodes WHERE notebook_id = ?", (notebook_id,))
    conn.execute("DELETE FROM canvas_edges WHERE notebook_id = ?", (notebook_id,))
    for n in nodes or []:
        conn.execute(
            "INSERT INTO canvas_nodes (id, notebook_id, x, y, kind, ref_type, ref_id, "
            "snapshot_json, title, z, width, height, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                n.get("id") or str(uuid.uuid4()),
                notebook_id,
                float(n.get("x", 0.0)),
                float(n.get("y", 0.0)),
                n.get("kind", "artifact"),
                n.get("ref_type"),
                n.get("ref_id"),
                json.dumps(n.get("snapshot", {}), default=str),
                n.get("title", ""),
                int(n.get("z", 0)),
                _as_float_or_none(n.get("width")),
                _as_float_or_none(n.get("height")),
                n.get("created_at") or now,
                now,
            ),
        )
    for e in edges or []:
        conn.execute(
            "INSERT INTO canvas_edges (id, notebook_id, source, target, state, label, "
            "meta_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                e.get("id") or str(uuid.uuid4()),
                notebook_id,
                e["source"],
                e["target"],
                e.get("state", "user"),
                e.get("label", ""),
                json.dumps(e.get("meta", {}), default=str),
                e.get("created_at") or now,
            ),
        )
    if viewport is not None:
        _save_viewport(conn, notebook_id, viewport.get("x", 0.0),
                       viewport.get("y", 0.0), viewport.get("zoom", 1.0))
    conn.commit()


def _patch_node(conn: sqlite3.Connection, notebook_id: str, node_id: str,
                x: float, y: float,
                width: Optional[float] = None, height: Optional[float] = None) -> bool:
    """Move (and optionally resize) a single node. width/height are updated only when
    supplied so a pure-drag patch never clobbers a previously-persisted size."""
    sets = ["x = ?", "y = ?", "updated_at = ?"]
    params: List[Any] = [float(x), float(y), _now()]
    if width is not None:
        sets.append("width = ?")
        params.append(float(width))
    if height is not None:
        sets.append("height = ?")
        params.append(float(height))
    params.extend([node_id, notebook_id])
    cur = conn.execute(
        f"UPDATE canvas_nodes SET {', '.join(sets)} WHERE id = ? AND notebook_id = ?",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def _upsert_edge(conn: sqlite3.Connection, notebook_id: str, edge: Dict[str, Any]) -> Dict[str, Any]:
    edge_id = edge.get("id") or str(uuid.uuid4())
    now = edge.get("created_at") or _now()
    conn.execute(
        "INSERT INTO canvas_edges (id, notebook_id, source, target, state, label, meta_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET source=excluded.source, target=excluded.target, "
        "state=excluded.state, label=excluded.label, meta_json=excluded.meta_json",
        (
            edge_id, notebook_id, edge["source"], edge["target"],
            edge.get("state", "user"), edge.get("label", ""),
            json.dumps(edge.get("meta", {}), default=str), now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM canvas_edges WHERE id = ?", (edge_id,)).fetchone()
    return _edge_to_dict(row)


def _delete_edge(conn: sqlite3.Connection, notebook_id: str, edge_id: str) -> bool:
    cur = conn.execute(
        "DELETE FROM canvas_edges WHERE id = ? AND notebook_id = ?", (edge_id, notebook_id)
    )
    conn.commit()
    return cur.rowcount > 0


def _save_viewport(conn: sqlite3.Connection, notebook_id: str,
                   x: float, y: float, zoom: float) -> None:
    conn.execute(
        "INSERT INTO canvas_viewport (notebook_id, x, y, zoom, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(notebook_id) DO UPDATE SET x=excluded.x, y=excluded.y, "
        "zoom=excluded.zoom, updated_at=excluded.updated_at",
        (notebook_id, float(x), float(y), float(zoom), _now()),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Public API — acquires the shared production connection (never raises).
# --------------------------------------------------------------------------
def _get_conn() -> sqlite3.Connection:
    global _SCHEMA_READY
    from storage.database import Database

    conn = Database().get_connection()
    if not _SCHEMA_READY:
        _ensure_schema(conn)
        _SCHEMA_READY = True
    return conn


def get_layout(notebook_id: str) -> Dict[str, Any]:
    try:
        return _get_layout(_get_conn(), notebook_id)
    except Exception as e:
        logger.warning(f"[canvas_layout] get_layout failed ({notebook_id}): {e}")
        return {"nodes": [], "edges": [], "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0}}


def save_layout(notebook_id: str, nodes, edges, viewport=None) -> bool:
    try:
        _save_layout(_get_conn(), notebook_id, nodes, edges, viewport)
        return True
    except Exception as e:
        logger.warning(f"[canvas_layout] save_layout failed ({notebook_id}): {e}")
        return False


def patch_node(notebook_id: str, node_id: str, x: float, y: float,
               width: Optional[float] = None, height: Optional[float] = None) -> bool:
    try:
        return _patch_node(_get_conn(), notebook_id, node_id, x, y, width, height)
    except Exception as e:
        logger.warning(f"[canvas_layout] patch_node failed ({node_id}): {e}")
        return False


def upsert_edge(notebook_id: str, edge: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return _upsert_edge(_get_conn(), notebook_id, edge)
    except Exception as e:
        logger.warning(f"[canvas_layout] upsert_edge failed ({notebook_id}): {e}")
        return None


def delete_edge(notebook_id: str, edge_id: str) -> bool:
    try:
        return _delete_edge(_get_conn(), notebook_id, edge_id)
    except Exception as e:
        logger.warning(f"[canvas_layout] delete_edge failed ({edge_id}): {e}")
        return False


def save_viewport(notebook_id: str, x: float, y: float, zoom: float) -> bool:
    try:
        _save_viewport(_get_conn(), notebook_id, x, y, zoom)
        return True
    except Exception as e:
        logger.warning(f"[canvas_layout] save_viewport failed ({notebook_id}): {e}")
        return False
