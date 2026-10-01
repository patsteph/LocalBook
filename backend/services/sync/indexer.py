"""Make synced sources searchable on this Mac (LB-12, 12j).

Each Mac keeps its own LanceDB index; sync moves the source rows, never the
vectors. So after an apply this Mac must embed what arrived. The old hook called
`reindex_notebook(force=False)`, which skips any source whose metadata says it
has chunks — and a synced source carries the OTHER Mac's chunk count, so it was
skipped every time and never became searchable here (found 2026-10-01: the
mini held 22 MBP sources with no index at all).

What needs work is decided by looking, not by trusting metadata:
  * missing   — a source that was indexed somewhere (`chunks > 0`) but has no
                rows in this Mac's table for its notebook
  * changed   — a source whose text an apply changed (engine report `reindex`)
  * removed   — a source an apply deleted (engine report `unindex`)
Changed/removed ids are persisted in sync.db so a restart does not lose them.

Embeddings only: HyDE is off and the stored summary is reused, so this never
puts the main model to work. Entity extraction is queued the normal way.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from services.sync import progress, store

logger = logging.getLogger(__name__)

_PENDING = "index_pending"
_DIRTY = "index_dirty"      # set when an apply touched sources; the diff only runs then
_task: Optional[asyncio.Task] = None
_again = False


# ── what arrived ─────────────────────────────────────────────────────────────


def note(report: Dict[str, Any]) -> bool:
    """Record what an apply changed. Returns True when there is indexing to do."""
    tables = report.get("tables") or {}
    changed = bool((tables.get("sources") or {}).get("changed"))
    re_ids, un_ids = report.get("reindex") or [], report.get("unindex") or []
    if re_ids or un_ids:
        p = store.get(_PENDING, {}) or {}
        p["reindex"] = sorted(set(p.get("reindex", [])) | set(re_ids))
        p["unindex"] = sorted(set(p.get("unindex", [])) | set(un_ids))
        store.put(_PENDING, p)
    dirty = changed or bool(re_ids or un_ids)
    if dirty:
        store.put(_DIRTY, True)
    return dirty


def mark_dirty() -> None:
    """Force one full look (startup catch-up)."""
    store.put(_DIRTY, True)


# ── what needs doing ─────────────────────────────────────────────────────────


def _indexed_ids(notebook_id: str) -> Set[str]:
    from services import rag_storage

    db = rag_storage._get_db()
    name = f"notebook_{notebook_id}"
    if name not in db.table_names():
        return set()
    t = db.open_table(name)
    try:
        col = t.to_arrow().column("source_id")
        return set(col.to_pylist())
    except Exception as exc:
        logger.debug("[sync-index] could not read %s: %s", name, exc)
        return set()


def _sources() -> List[Dict[str, Any]]:
    from storage.database import get_db

    conn = get_db().get_connection()
    rows = conn.execute(
        "SELECT id, notebook_id, filename, type, format, metadata_json FROM sources "
        "WHERE content IS NOT NULL AND content != ''").fetchall()
    out = []
    for sid, nb, filename, typ, fmt, meta in rows:
        try:
            m = json.loads(meta or "{}") or {}
        except Exception:
            m = {}
        out.append({"id": sid, "notebook_id": nb, "filename": filename or "Unknown",
                    "type": typ or fmt or "document", "chunks": int(m.get("chunks") or 0),
                    "status": m.get("status") or "completed", "summary": m.get("summary") or ""})
    return out


def plan() -> Dict[str, Any]:
    """The work, decided by looking at the index. Sync (call it in a thread)."""
    p = store.get(_PENDING, {}) or {}
    re_ids = set(p.get("reindex", []))
    srcs = [s for s in _sources() if s["status"] == "completed"]
    by_nb: Dict[str, List[Dict[str, Any]]] = {}
    for s in srcs:
        by_nb.setdefault(s["notebook_id"], []).append(s)
    todo = []
    for nb, items in by_nb.items():
        have = _indexed_ids(nb)
        for s in items:
            if s["id"] in re_ids or (s["chunks"] > 0 and s["id"] not in have):
                todo.append(s)
    present = {s["id"] for s in srcs}
    gone = [sid for sid in p.get("unindex", []) if sid not in present]
    return {"index": todo, "unindex": gone}


# ── doing it ─────────────────────────────────────────────────────────────────


async def run_into(run, paced: bool = False) -> Dict[str, Any]:
    """Index what is pending, reporting into `run` (phase "index"). Stoppable
    between sources; anything not reached stays pending for next time.

    paced: a run the user did not start (startup catch-up, the background loop,
    another Mac's push) rests between sources while the user is active, so a
    100-source catch-up trickles instead of pinning the Mac."""
    from services.rag_engine import rag_engine
    from storage.source_store import source_store

    if not store.get(_DIRTY, False):
        # Nothing arrived since the last full look — don't scan every index each minute.
        run.step("index", total=0, unit="sources")
        return {"indexed": 0, "failed": 0, "removed": 0}
    work = await asyncio.to_thread(plan)
    todo, gone = work["index"], work["unindex"]
    run.step("index", total=len(todo), unit="sources")
    removed = 0
    for sid in gone:
        for nb in await asyncio.to_thread(_tables):
            try:
                if await rag_engine.delete_source(nb, sid):
                    removed += 1
            except Exception:
                pass
    _forget(unindex=gone)
    indexed = failed = 0
    for s in todo:
        run.check()
        try:
            content = await source_store.get_content(s["notebook_id"], s["id"])
            text = (content or {}).get("content") or ""
            if text:
                await rag_engine.delete_source(s["notebook_id"], s["id"])
                await rag_engine.ingest_document(
                    notebook_id=s["notebook_id"], source_id=s["id"], text=text,
                    filename=s["filename"], source_type=s["type"],
                    enable_hyde=False, precomputed_summary=s["summary"], deferred=True)
                indexed += 1
            _forget(reindex=[s["id"]])
        except Exception as exc:
            failed += 1
            logger.warning("[sync-index] %s (%s) not indexed yet: %s", s["filename"], s["id"], exc)
        run.advance(1, detail=s["filename"])
        if paced:
            await asyncio.sleep(_pace())
    # A source that failed is retried at the next startup catch-up or the next sync that
    # brings sources — not every minute: a permanently failing one would rescan forever.
    store.put(_DIRTY, False)
    out = {"indexed": indexed, "failed": failed, "removed": removed}
    if indexed or removed or failed:
        logger.info("[sync-index] %s", out)
    return out


def _pace() -> float:
    try:
        from services import presence
        return presence.background_pace_seconds()
    except Exception:
        return 2.0


def _tables() -> List[str]:
    from services import rag_storage

    return [n[len("notebook_"):] for n in rag_storage._get_db().table_names() if n.startswith("notebook_")]


def _forget(reindex: Iterable[str] = (), unindex: Iterable[str] = ()) -> None:
    re_ids, un_ids = set(reindex), set(unindex)
    if not re_ids and not un_ids:
        return
    p = store.get(_PENDING, {}) or {}
    p["reindex"] = [x for x in p.get("reindex", []) if x not in re_ids]
    p["unindex"] = [x for x in p.get("unindex", []) if x not in un_ids]
    store.put(_PENDING, p)


def kick(delay: float = 5.0) -> None:
    """Index soon, as its own visible run — after an incoming sync, a background
    sync, or at startup. Coalesces: a kick while running runs once more after."""
    global _task, _again
    if _task and not _task.done():
        _again = True
        return
    from utils.tasks import safe_create_task

    _task = safe_create_task(_loop(delay), name="sync-index")


async def _loop(delay: float) -> None:
    global _again
    await asyncio.sleep(delay)
    while True:
        _again = False
        if progress.active("initiated"):
            # A user-started run indexes inside itself; wait for it, then re-check.
            await asyncio.sleep(5)
            _again = True
        elif store.get(_DIRTY, False):
            work = await asyncio.to_thread(plan)
            if not (work["index"] or work["unindex"]):
                store.put(_DIRTY, False)
            else:
                run = progress.begin("index", "index", ["index"])
                try:
                    run.finish(result=await run_into(run, paced=True))
                except progress.Cancelled:
                    run.finish(result={"stopped": True})
                except Exception as exc:
                    run.finish(error=f"{type(exc).__name__}: {exc}"[:300])
        if not _again:
            return
