"""What a paired phone may read of the library: chunk text and the Mac's own vectors.

Mac-to-Mac sync moves source rows and each Mac re-embeds; a phone should not have to (it would need
the chunker and a full re-embed). Test 1 proved a phone can use this Mac's vectors (cosine 0.9999),
so the phone pulls ready-made chunks with their vectors and embeds only the questions it is asked.

Read-only. Three calls, all signed by the phone (peer._verified_phone):
  manifest  the embedder key + each visible notebook with a content digest
  sources   one notebook's sources, each with a content hash (the phone diffs against its own)
  chunks    a page of chunks (text + float16 vector) for a notebook, optionally for chosen sources

Privacy: every notebook is visible unless the owner switched it to "Keep off phones". The switch is
a per-Mac setting (sync.db), not a synced one. Excluded notebooks are indistinguishable from missing
ones to the phone.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROTO = 1
EXCLUDED_KEY = "phone_excluded_notebooks"
MAX_PAGE = 400
_digest_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
_cache_lock = threading.Lock()


# ── who may see what ─────────────────────────────────────────────────────────


def excluded() -> List[str]:
    from services.sync import store
    return list(store.get(EXCLUDED_KEY, []) or [])


def set_excluded(notebook_ids: List[str]) -> List[str]:
    from services.sync import store
    clean = sorted({str(i) for i in notebook_ids})
    store.put(EXCLUDED_KEY, clean)
    return clean


def notebook_titles() -> Dict[str, str]:
    """{notebook id: title} from the main database (read-only)."""
    from services.sync import runtime
    path = runtime.data_dir() / runtime.DB_FILES["main"]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT id, title FROM notebooks")}
    finally:
        conn.close()


def settings_view() -> List[Dict[str, Any]]:
    """For the Settings screen: every notebook and whether phones may see it."""
    off = set(excluded())
    return [{"id": i, "title": t, "on_phone": i not in off}
            for i, t in sorted(notebook_titles().items(), key=lambda kv: kv[1].lower())]


def _visible(notebook_id: str) -> bool:
    return notebook_id in notebook_titles() and notebook_id not in set(excluded())


def is_visible(notebook_id: str) -> bool:
    return _visible(notebook_id)


def shape_answer(result: Dict[str, Any]) -> Dict[str, Any]:
    """What a phone needs from the Mac's answer: the text, the sources it cited, and the follow-ups.
    Parent-document context is left out (it can be large and the phone does not use it)."""
    cites = []
    for c in result.get("citations") or []:
        cites.append({"number": c.get("number"), "source_id": c.get("source_id"), "filename": c.get("filename"),
                      "chunk_index": c.get("chunk_index"), "text": c.get("text", ""),
                      "confidence": c.get("confidence"), "confidence_level": c.get("confidence_level")})
    return {"answer": result.get("answer", ""), "citations": cites,
            "follow_up_questions": list(result.get("follow_up_questions") or []),
            "low_confidence": bool(result.get("low_confidence")), "cache_hit": bool(result.get("cache_hit"))}


# ── the index, read directly ─────────────────────────────────────────────────


def _db():
    import lancedb
    from services import embedding_migration as em
    return lancedb.connect(str(em._live_dir()))


def _table(notebook_id: str):
    from services import embedding_migration as em
    name = f"notebook_{notebook_id}"
    if name not in em._tables(em._live_dir()):
        return None
    return _db().open_table(name)


def embedder_key() -> Dict[str, Any]:
    from config import settings
    from services import embedding_profile
    model = getattr(settings, "embedding_model", "") or ""
    prof = embedding_profile.for_model(model)
    return {"model_id": model, "dims": prof.dims, "max_tokens": prof.max_tokens, "pooling": prof.pooling,
            "document_template": prof.document_template, "query_template": prof.query_template,
            "vector": "float16-le-base64"}


def _vec16(vector) -> str:
    import numpy as np
    return base64.b64encode(np.asarray(vector, dtype="<f2").tobytes()).decode()


def _row_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _summaries(table) -> Dict[str, Dict[str, Any]]:
    """Per-source digest of the chunks currently indexed, cached by table version."""
    version = int(table.version)
    key = (table.name, version)
    with _cache_lock:
        hit = _digest_cache.get(key)
    if hit is not None:
        return hit
    rows = table.to_arrow().select(["source_id", "chunk_index", "text", "filename", "source_type"]).to_pylist()
    per: Dict[str, Dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: (r["source_id"], r["chunk_index"])):
        s = per.setdefault(r["source_id"], {"source_id": r["source_id"], "filename": r["filename"],
                                            "source_type": r["source_type"], "chunks": 0, "_h": hashlib.sha256()})
        s["chunks"] += 1
        s["_h"].update(f'{r["chunk_index"]}:{_row_hash(r["text"])};'.encode())
    out = {sid: {**{k: v for k, v in s.items() if k != "_h"}, "hash": s["_h"].hexdigest()[:24]}
           for sid, s in per.items()}
    with _cache_lock:
        for k in [k for k in _digest_cache if k[0] == table.name]:
            _digest_cache.pop(k, None)
        _digest_cache[key] = out
    return out


_rows_cache: Dict[str, Any] = {}          # one entry: the table last paged, keyed by (name, version)


def _sorted_rows(table) -> List[Dict[str, Any]]:
    """The table's chunks in (source_id, chunk_index) order, kept while its version is unchanged so
    paging through a notebook reads it once, not once per page."""
    key = (table.name, int(table.version))
    with _cache_lock:
        if _rows_cache.get("key") == key:
            return _rows_cache["rows"]
    rows = table.to_arrow().select(["source_id", "chunk_index", "text", "filename", "source_type", "vector"]).to_pylist()
    rows.sort(key=lambda r: (r["source_id"], r["chunk_index"]))
    with _cache_lock:
        _rows_cache["key"], _rows_cache["rows"] = key, rows
    return rows


def manifest() -> Dict[str, Any]:
    titles = notebook_titles()
    off = set(excluded())
    notebooks = []
    for nid, title in sorted(titles.items(), key=lambda kv: kv[1].lower()):
        if nid in off:
            continue
        table = _table(nid)
        if table is None:
            continue
        sums = _summaries(table)
        digest = hashlib.sha256("".join(f'{sid}:{s["hash"]};' for sid, s in sorted(sums.items())).encode()).hexdigest()[:24]
        notebooks.append({"id": nid, "title": title, "sources": len(sums),
                          "chunks": sum(s["chunks"] for s in sums.values()), "digest": digest})
    return {"proto": PROTO, "embedder": embedder_key(), "notebooks": notebooks}


def sources(notebook_id: str) -> Optional[Dict[str, Any]]:
    if not _visible(notebook_id):
        return None
    table = _table(notebook_id)
    if table is None:
        return None
    return {"notebook_id": notebook_id, "sources": list(_summaries(table).values())}


def chunks(notebook_id: str, *, source_ids: Optional[List[str]] = None,
           after: Optional[Dict[str, Any]] = None, limit: int = 200) -> Optional[Dict[str, Any]]:
    """One page of chunks in (source_id, chunk_index) order. `after` is the last row of the previous
    page: {"source_id": …, "chunk_index": …}. Vectors are float16, little-endian, base64."""
    if not _visible(notebook_id):
        return None
    table = _table(notebook_id)
    if table is None:
        return None
    limit = max(1, min(int(limit), MAX_PAGE))
    want = set(source_ids) if source_ids else None
    key = embedder_key()
    rows = [r for r in _sorted_rows(table) if (want is None or r["source_id"] in want)]
    if after:
        pos = (after.get("source_id", ""), int(after.get("chunk_index", -2)))
        rows = [r for r in rows if (r["source_id"], r["chunk_index"]) > pos]
    page = rows[:limit]
    out = []
    for r in page:
        if r["vector"] is None or len(r["vector"]) != key["dims"]:
            continue                                   # never send a vector of the wrong shape
        text = r["text"]
        summary = r["chunk_index"] == -1 and text.startswith("[SUMMARY] ")
        out.append({"source_id": r["source_id"], "chunk_index": r["chunk_index"],
                    "text": text[len("[SUMMARY] "):] if summary else text, "summary": summary,
                    "filename": r["filename"], "source_type": r["source_type"], "vector": _vec16(r["vector"])})
    more = len(rows) > limit
    last = ({"source_id": page[-1]["source_id"], "chunk_index": page[-1]["chunk_index"]} if page else None)
    return {"notebook_id": notebook_id, "chunks": out, "more": more, "after": last}
