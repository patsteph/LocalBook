"""The topics of one notebook, for a peer's sidebar: what the desktop's Themes panel shows, compact.

Pure function of the topic model's state (services/topic_modeling): per topic the display name, a few keywords,
and how many of THIS notebook's chunks and sources it covers, biggest first. Nothing is computed here; a notebook
whose topics were never modelled simply has none.
"""
from typing import Any, Dict, List

from services.sync import phone_library


async def topics(notebook_id: str, limit: int = 12) -> List[Dict[str, Any]]:
    if not phone_library.is_visible(notebook_id):
        return []
    try:
        from services.topic_modeling import topic_modeling_service as svc
        found = await svc.get_topics(notebook_id)
    except Exception:
        return []
    chunks: Dict[int, int] = {}
    sources: Dict[int, set] = {}
    for d in getattr(svc, "_documents", []):
        if d.notebook_id == notebook_id and d.topic_id != -1:
            chunks[d.topic_id] = chunks.get(d.topic_id, 0) + 1
            sources.setdefault(d.topic_id, set()).add(d.source_id)
    out = []
    for t in found:
        n = chunks.get(t.topic_id, 0)
        if n == 0:
            continue
        out.append({"id": str(t.topic_id), "name": t.display_name, "keywords": [k for k, _ in t.keywords[:8]],
                    "chunks": n, "sources": len(sources.get(t.topic_id, ()))})
    out.sort(key=lambda x: (-x["chunks"], x["name"]))
    return out[:limit]
