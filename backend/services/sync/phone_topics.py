"""The topics of one notebook, for a peer's sidebar: the source tags the desktop's Sources panel shows as a cloud,
counted across the notebook's sources, biggest first.

Read straight from localbook.db (sources.tags is a JSON list per source), so it needs no model and costs one query.
A notebook whose sources were never tagged simply has none.
"""
import json
import sqlite3
from typing import Any, Dict, List

from services.sync import phone_library


def topics(notebook_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    return cloud(notebook_id, limit)["topics"]


def cloud(notebook_id: str, limit: int = 200) -> Dict[str, Any]:
    """{"topics": [...], "sources": n, "untagged": n}: the whole Tag cloud line of the desktop's Sources panel."""
    if not phone_library.is_visible(notebook_id):
        return {"topics": [], "sources": 0, "untagged": 0}
    from services.sync import runtime
    path = runtime.data_dir() / runtime.DB_FILES["main"]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute("SELECT tags FROM sources WHERE notebook_id = ?", (notebook_id,)).fetchall()
    except sqlite3.Error:
        return {"topics": [], "sources": 0, "untagged": 0}
    finally:
        conn.close()
    counts: Dict[str, int] = {}
    untagged = 0
    for (raw,) in rows:
        try:
            tags = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            tags = []
        clean = {str(t).strip() for t in (tags or []) if str(t).strip()}
        if not clean:
            untagged += 1
        for t in clean:
            counts[t] = counts.get(t, 0) + 1
    out = [{"id": t, "name": t, "keywords": [], "chunks": 0, "sources": n} for t, n in counts.items()]
    out.sort(key=lambda x: (-x["sources"], x["name"]))
    return {"topics": out[:limit], "sources": len(rows), "untagged": untagged}
