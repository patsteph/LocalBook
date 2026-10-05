"""First contact between two Macs that both already have data (LB-12f, D12).

User decision 2026-09-30: **match + merge, live over the pairing link** — not an
archive import. The Mac that JOINS (the one that asked to pair) adopts the
seed's identity for what they both have:

  * notebooks matched by title (trimmed, case-insensitive, and only when the
    title is unique on both sides — an ambiguous match is no match);
  * sources matched by `content_hash` (D16: the hash of the extracted TEXT)
    inside a matched notebook.

Matched rows are RE-KEYED on the joiner to the seed's ids, in one transaction,
children included; the normal merge then treats them as one record (fields
that differ become ordinary conflict items). Everything unmatched is simply new.
The preview is computed from the same function, so what the user approves is
what runs.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List


def index(conn: sqlite3.Connection) -> Dict[str, Any]:
    """What a seed tells a joiner: its notebooks and sources' identities."""
    nbs = [{"id": i, "title": t} for i, t in conn.execute("SELECT id, title FROM notebooks")]
    srcs = [{"id": i, "notebook_id": n, "content_hash": h}
            for i, n, h in conn.execute("SELECT id, notebook_id, content_hash FROM sources")]
    return {"notebooks": nbs, "sources": srcs}


def _norm(title) -> str:
    return " ".join(str(title or "").split()).casefold()


def plan(local: Dict[str, Any], seed: Dict[str, Any]) -> Dict[str, Any]:
    """Which local ids become which seed ids, plus the counts for the preview."""
    def unique_titles(nbs):
        seen: Dict[str, List[str]] = {}
        for nb in nbs:
            seen.setdefault(_norm(nb["title"]), []).append(nb["id"])
        return {t: ids[0] for t, ids in seen.items() if len(ids) == 1 and t}

    lt, st = unique_titles(local["notebooks"]), unique_titles(seed["notebooks"])
    nb_map = {lt[t]: st[t] for t in lt.keys() & st.keys() if lt[t] != st[t]}
    same_nb = {lt[t] for t in lt.keys() & st.keys() if lt[t] == st[t]}

    seed_src = {}
    for s in seed["sources"]:
        if s.get("content_hash"):
            seed_src.setdefault((s["notebook_id"], s["content_hash"]), s["id"])
    src_map = {}
    for s in local["sources"]:
        nb = nb_map.get(s["notebook_id"], s["notebook_id"] if s["notebook_id"] in same_nb else None)
        if nb is None or not s.get("content_hash"):
            continue
        target = seed_src.get((nb, s["content_hash"]))
        if target and target != s["id"]:
            src_map[s["id"]] = target

    matched_nb = len(nb_map) + len(same_nb)
    return {
        "notebooks": nb_map, "sources": src_map,
        "counts": {
            "notebooks_here": len(local["notebooks"]), "notebooks_there": len(seed["notebooks"]),
            "notebooks_matched": matched_nb,
            "notebooks_after": len(local["notebooks"]) + len(seed["notebooks"]) - matched_nb,
            "sources_here": len(local["sources"]), "sources_there": len(seed["sources"]),
            "sources_matched": len(src_map),
        },
    }


def _columns(conn, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def rekey(conn: sqlite3.Connection, the_plan: Dict[str, Any], data_dir: Path) -> Dict[str, int]:
    """Apply the plan on the joiner, in ONE transaction (foreign keys off on
    this connection; every reference is updated explicitly)."""
    nb_map, src_map = the_plan["notebooks"], the_plan["sources"]
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if not r[0].startswith(("_", "sqlite_"))]
    done = {"notebooks": 0, "sources": 0, "references": 0}
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for old, new in src_map.items():
            for t in tables:
                for col in ("source_id", "saved_as_source_id"):
                    if col in _columns(conn, t):
                        done["references"] += conn.execute(
                            f'UPDATE "{t}" SET "{col}"=? WHERE "{col}"=?', (new, old)).rowcount
            done["sources"] += conn.execute("UPDATE sources SET id=? WHERE id=?", (new, old)).rowcount
        for old, new in nb_map.items():
            for t in tables:
                if t != "notebooks" and "notebook_id" in _columns(conn, t):
                    done["references"] += conn.execute(
                        f'UPDATE "{t}" SET notebook_id=? WHERE notebook_id=?', (new, old)).rowcount
            done["notebooks"] += conn.execute("UPDATE notebooks SET id=? WHERE id=?", (new, old)).rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    # Per-notebook files follow their notebook (collector.yaml, quiz cards).
    for old, new in nb_map.items():
        src, dst = data_dir / "notebooks" / old, data_dir / "notebooks" / new
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.iterdir():
                if not (dst / f.name).exists():
                    shutil.move(str(f), str(dst / f.name))
            shutil.rmtree(src, ignore_errors=True)
        cards = data_dir / "quizzes" / f"{old}_cards.json"
        if cards.exists() and not (data_dir / "quizzes" / f"{new}_cards.json").exists():
            cards.rename(data_dir / "quizzes" / f"{new}_cards.json")
    return done


def rekey_recall(conn: sqlite3.Connection, the_plan: Dict[str, Any]) -> int:
    n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for old, new in the_plan["notebooks"].items():
            for t in ("recall_entries", "conversation_summaries"):
                if "notebook_id" in _columns(conn, t):
                    n += conn.execute(f'UPDATE "{t}" SET notebook_id=? WHERE notebook_id=?', (new, old)).rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return n
