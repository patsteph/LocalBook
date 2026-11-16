"""The topics of one notebook, for a peer's sidebar: the source tags the desktop's Sources panel shows as a cloud,
counted across the notebook's sources, biggest first.

Read straight from localbook.db (sources.tags is a JSON list per source), so it needs no model and costs one query.
A notebook whose sources were never tagged simply has none.
"""
import json
import sqlite3
from typing import Any, Dict, List

from services.sync import phone_library


def topics(notebook_id: str, limit: int = 24) -> List[Dict[str, Any]]:
    if not phone_library.is_visible(notebook_id):
        return []
    from services.sync import runtime
    path = runtime.data_dir() / runtime.DB_FILES["main"]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute("SELECT tags FROM sources WHERE notebook_id = ?", (notebook_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    counts: Dict[str, int] = {}
    for (raw,) in rows:
        try:
            tags = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            continue
        for t in {str(t).strip() for t in tags if str(t).strip()}:
            counts[t] = counts.get(t, 0) + 1
    out = [{"id": t, "name": t, "keywords": [], "chunks": 0, "sources": n} for t, n in counts.items()]
    out.sort(key=lambda x: (-x["sources"], x["name"]))
    return out[:limit]
