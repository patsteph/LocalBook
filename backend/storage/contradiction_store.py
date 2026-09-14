"""Contradiction Store — durable "these two sources disagree" reports.

Contradiction detection was an expensive LLM scan (claim extraction + pairwise checks over up
to 10 sources) whose entire result lived in `_contradiction_cache`, a module-level dict. Every
backend restart threw it away, so the report had to be regenerated to be read, and
`dismiss_contradiction` — the user telling us "this one isn't real" — was forgotten on the next
launch. That also made the data unusable as a canvas signal: a map cannot be seeded from a
cache that may be empty.

SQLite in the shared `localbook.db`, mirroring the `canvas_layout_store` pattern: lazy
idempotent schema, thread-local connection via `Database()`, never raises (a storage failure
must degrade to "no cached report", never break a scan).

One row per contradiction; the report header is derived from them plus a small per-notebook
scan row, so a re-scan replaces cleanly while user dismissals are preserved by id.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA_READY = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS contradiction_scans (
            notebook_id      TEXT PRIMARY KEY,
            generated_at     TEXT NOT NULL,
            claims_analyzed  INTEGER DEFAULT 0,
            sources_analyzed INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS contradictions (
            id                 TEXT PRIMARY KEY,
            notebook_id        TEXT NOT NULL,
            source_a_id        TEXT,
            source_b_id        TEXT,
            contradiction_type TEXT DEFAULT '',
            severity           TEXT DEFAULT '',
            explanation        TEXT DEFAULT '',
            resolution_hint    TEXT,
            payload_json       TEXT DEFAULT '{}',
            detected_at        TEXT NOT NULL,
            dismissed          INTEGER DEFAULT 0,
            resolved           INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_contradictions_nb ON contradictions(notebook_id)"
    )
    # The canvas joins tension edges on source pairs, not on claim ids.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_contradictions_sources "
        "ON contradictions(notebook_id, source_a_id, source_b_id)"
    )
    conn.commit()


def _conn() -> Optional[sqlite3.Connection]:
    global _SCHEMA_READY
    try:
        from storage.database import Database

        conn = Database().get_connection()
        if not _SCHEMA_READY:
            _ensure_schema(conn)
            _SCHEMA_READY = True
        return conn
    except Exception as e:
        logger.warning(f"[ContradictionStore] connection failed: {e}")
        return None


def save_report(notebook_id: str, report: Dict[str, Any]) -> bool:
    """Persist a scan, replacing the notebook's previous one.

    PRESERVES USER DISMISSALS across a re-scan: a contradiction that keeps the same id was
    already judged by the user, and silently resurrecting it would be the system arguing with
    them. Dismissed/resolved flags are read back before the delete and re-applied.
    """
    conn = _conn()
    if conn is None:
        return False
    try:
        prior = {
            r["id"]: (r["dismissed"], r["resolved"])
            for r in conn.execute(
                "SELECT id, dismissed, resolved FROM contradictions WHERE notebook_id = ?",
                (notebook_id,),
            ).fetchall()
        }
        conn.execute("DELETE FROM contradictions WHERE notebook_id = ?", (notebook_id,))
        for c in report.get("contradictions", []) or []:
            cid = str(c.get("id") or "")
            if not cid:
                continue
            was = prior.get(cid, (0, 0))
            claim_a = c.get("claim_a") or {}
            claim_b = c.get("claim_b") or {}
            conn.execute(
                """INSERT OR REPLACE INTO contradictions
                   (id, notebook_id, source_a_id, source_b_id, contradiction_type, severity,
                    explanation, resolution_hint, payload_json, detected_at, dismissed, resolved)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid,
                    notebook_id,
                    str(claim_a.get("source_id") or ""),
                    str(claim_b.get("source_id") or ""),
                    c.get("contradiction_type") or "",
                    c.get("severity") or "",
                    c.get("explanation") or "",
                    c.get("resolution_hint"),
                    json.dumps(c, default=str),
                    c.get("detected_at") or datetime.utcnow().isoformat(),
                    1 if (c.get("dismissed") or was[0]) else 0,
                    1 if (c.get("resolved") or was[1]) else 0,
                ),
            )
        conn.execute(
            """INSERT INTO contradiction_scans
               (notebook_id, generated_at, claims_analyzed, sources_analyzed)
               VALUES (?,?,?,?)
               ON CONFLICT(notebook_id) DO UPDATE SET
                 generated_at = excluded.generated_at,
                 claims_analyzed = excluded.claims_analyzed,
                 sources_analyzed = excluded.sources_analyzed""",
            (
                notebook_id,
                report.get("generated_at") or datetime.utcnow().isoformat(),
                int(report.get("claims_analyzed") or 0),
                int(report.get("sources_analyzed") or 0),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[ContradictionStore] save_report failed: {e}")
        return False


def load_report(notebook_id: str) -> Optional[Dict[str, Any]]:
    """The stored report, or None if this notebook has never been scanned."""
    conn = _conn()
    if conn is None:
        return None
    try:
        head = conn.execute(
            "SELECT generated_at, claims_analyzed, sources_analyzed "
            "FROM contradiction_scans WHERE notebook_id = ?",
            (notebook_id,),
        ).fetchone()
        if not head:
            return None
        rows = conn.execute(
            "SELECT payload_json, dismissed, resolved FROM contradictions WHERE notebook_id = ?",
            (notebook_id,),
        ).fetchall()
        contradictions: List[Dict[str, Any]] = []
        for r in rows:
            try:
                c = json.loads(r["payload_json"] or "{}")
            except Exception:
                continue
            # The columns are authoritative for these two — they can be updated after the scan.
            c["dismissed"] = bool(r["dismissed"])
            c["resolved"] = bool(r["resolved"])
            contradictions.append(c)
        return {
            "notebook_id": notebook_id,
            "generated_at": head["generated_at"],
            "contradictions": contradictions,
            "claims_analyzed": head["claims_analyzed"] or 0,
            "sources_analyzed": head["sources_analyzed"] or 0,
        }
    except Exception as e:
        logger.warning(f"[ContradictionStore] load_report failed: {e}")
        return None


def set_flag(notebook_id: str, contradiction_id: str, field: str, value: bool) -> bool:
    """Mark one contradiction dismissed/resolved. Survives a restart — that is the point."""
    if field not in ("dismissed", "resolved"):
        return False
    conn = _conn()
    if conn is None:
        return False
    try:
        cur = conn.execute(
            f"UPDATE contradictions SET {field} = ? WHERE id = ? AND notebook_id = ?",
            (1 if value else 0, contradiction_id, notebook_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[ContradictionStore] set_flag failed: {e}")
        return False


def clear(notebook_id: str) -> bool:
    conn = _conn()
    if conn is None:
        return False
    try:
        conn.execute("DELETE FROM contradictions WHERE notebook_id = ?", (notebook_id,))
        conn.execute("DELETE FROM contradiction_scans WHERE notebook_id = ?", (notebook_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[ContradictionStore] clear failed: {e}")
        return False


def source_pairs(notebook_id: str, *, include_dismissed: bool = False) -> List[Dict[str, Any]]:
    """Distinct disagreeing SOURCE pairs — what the canvas draws tension edges from.

    Collapses many claim-level contradictions between the same two sources into one pair,
    keeping the worst severity, so two sources that clash on six points get one edge rather
    than six. Dismissed ones are excluded by default: the user already said they aren't real.
    """
    conn = _conn()
    if conn is None:
        return []
    try:
        sql = (
            "SELECT source_a_id, source_b_id, severity, contradiction_type, explanation "
            "FROM contradictions WHERE notebook_id = ? AND source_a_id != '' AND source_b_id != '' "
            "AND source_a_id != source_b_id"
        )
        if not include_dismissed:
            sql += " AND dismissed = 0"
        rows = conn.execute(sql, (notebook_id,)).fetchall()
        rank = {"high": 3, "medium": 2, "low": 1}
        best: Dict[tuple, Dict[str, Any]] = {}
        for r in rows:
            a, b = r["source_a_id"], r["source_b_id"]
            key = (a, b) if a <= b else (b, a)
            sev = (r["severity"] or "").lower()
            cur = best.get(key)
            if cur is None or rank.get(sev, 0) > rank.get(cur["severity"], 0):
                best[key] = {
                    "source_a_id": key[0],
                    "source_b_id": key[1],
                    "severity": sev,
                    "contradiction_type": r["contradiction_type"] or "",
                    "explanation": r["explanation"] or "",
                }
        return list(best.values())
    except Exception as e:
        logger.warning(f"[ContradictionStore] source_pairs failed: {e}")
        return []
