"""Tell a device when it searches with a different embedding model than its library.

Vectors never sync — every device embeds its own index — so a device that joins (a new Mac,
a fresh install, later the iPhone app) or that stayed on an older model still WORKS, but the
same question finds different passages there. The fix is the user's to make (a download, a
re-index), so this only says so, plainly, wherever the user will see it: Settings › Sync, Data
Health, and `GET /sync/status` → `embedder_advice` for any other client (the iPhone app).

The library's model is what most of its devices use (this one included); a tie goes to the
collector Mac's model — the Mac that ingests most of the library. Models are learned from the
sync hello, which both ends exchange on every connection.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def record_peer_models(device_id: str, models: Optional[Dict[str, str]]) -> None:
    from services.sync import store
    if models:
        store.put(f"models:{device_id}", dict(models))


def _peers() -> List[Dict[str, Any]]:
    from services.sync import store
    out = []
    for d in store.devices():
        m = (store.get(f"models:{d['device_id']}") or {}).get("embedding_model")
        if m:
            out.append({"device_id": d["device_id"], "name": d.get("name") or d["device_id"], "model": m})
    return out


def advice() -> Optional[Dict[str, Any]]:
    """None when this device already searches like its library (or there is no library)."""
    try:
        from config import settings
        from services.sync import roles, store

        if not store.enabled():
            return None
        peers = _peers()
        if not peers:
            return None
        mine = settings.embedding_model
        votes = Counter([mine] + [p["model"] for p in peers])
        top = votes.most_common()
        library = top[0][0]
        if len(top) > 1 and top[0][1] == top[1][1]:            # tie → the collector Mac's model
            chosen = roles.collector() or {}
            coll = next((p["model"] for p in peers if p["device_id"] == chosen.get("device_id")), None)
            library = coll or (mine if top[0][1] == votes[mine] else library)
        if library == mine:
            return None

        from services import embedding_migration
        from services.model_presence import is_present
        mig = embedding_migration.status()
        building = mig.get("target") == library and (mig.get("running") or mig.get("ready")
                                                     or mig.get("switch_pending"))
        short = library.rsplit("/", 1)[-1]
        downloaded = is_present(library)
        return {
            "library_model": library,
            "this_device": mine,
            "devices": [p["name"] for p in peers if p["model"] == library],
            "downloaded": downloaded,
            "in_progress": bool(building),
            "message": (f"Your other devices search with {short}. "
                        + ("The new search index is being prepared on this one." if building else
                           f"Switch this one to {short} so the same question finds the same passages."
                           if downloaded else
                           f"Download {short} and re-index this device so search matches.")),
        }
    except Exception as exc:
        logger.debug("[sync] embedder advice unavailable: %s", exc)
        return None
