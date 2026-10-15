"""Switch the embedding model without losing search for a minute (EmbeddingGemma 2 plan, phase 2).

The old way — change the setting, restart, drop every table, re-ingest every source — left
search empty until each notebook was redone, asked the fast model for every synthetic
question and summary again, and could not resume. This instead:

  1. **Builds a shadow index** (`lancedb@<model>` beside the live `lancedb`) in the
     background while search keeps using the live one. It RE-EMBEDS THE ROWS ALREADY IN THE
     INDEX — the same text ingest embedded (chunk + its stored synthetic questions; a summary
     row's summary) in the new model's document format — so every chunk survives as it is,
     including content that exists only in the index (image descriptions), and NO LLM call
     is made. Embedding only, paced for the user, through the GPU budget.
  2. **Resumes** table by table (a ledger in the data dir); a table that changed since it
     was copied (a source added, synced in, deleted) is copied again before the switch.
  3. **Flips at startup** — never under a running app: on the user's click a marker is
     written and the app restarts; before anything opens LanceDB the live folder becomes
     `lancedb@<old model>`, the shadow becomes `lancedb`, and the saved embedder changes.
     Every other vector store heals itself on that launch (phase 1: archival memory
     re-embeds from its records, articles and canvas centroids per model, the topic model
     is set aside, caches are per model). Sources that arrived after the last copy are
     found by the sync indexer's "missing from this index" check.
  4. **Rolls back** the same way, while the previous index is kept; Data Health offers to
     remove it.

Per Mac: vectors never sync, so each Mac migrates itself (scenario matrix S1–S14,
READFIRST/in-progress/embeddinggemma2-switch.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BATCH = 64
LEDGER = ".embedder_migration.json"     # the shadow build's state (resumable)
FLIP = ".embedder_flip.json"            # "switch on next launch"
ROLLBACK = ".embedder_rollback.json"    # "switch back on next launch"
HISTORY = ".embedder_history.json"      # the last switch, for rollback + cleanup

_task: Optional[asyncio.Task] = None
_stop = False


# ── paths ────────────────────────────────────────────────────────────────────


def _data_dir() -> Path:
    from config import settings
    return Path(settings.data_dir)


def _live_dir() -> Path:
    from config import settings
    return Path(settings.db_path)


def slug(model: str) -> str:
    return (model or "unknown").rsplit("/", 1)[-1].replace(" ", "_")


def shadow_dir(model: str) -> Path:
    live = _live_dir()
    return live.with_name(f"{live.name}@{slug(model)}")


def _read(name: str) -> Dict[str, Any]:
    try:
        return json.loads((_data_dir() / name).read_text())
    except Exception:
        return {}


def _write(name: str, body: Dict[str, Any]) -> None:
    p = _data_dir() / name
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2))
    os.replace(tmp, p)


def _drop(name: str) -> None:
    (_data_dir() / name).unlink(missing_ok=True)


def _tables(db_dir: Path) -> List[str]:
    """Notebook tables by DIRECTORY — LanceDB's table_names() under-reports on this format."""
    if not db_dir.is_dir():
        return []
    return sorted(d[: -len(".lance")] for d in os.listdir(db_dir)
                  if d.startswith("notebook_") and d.endswith(".lance"))


def _dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0


# ── what gets embedded (mirrors rag_storage exactly) ─────────────────────────


def embedded_text(row: Dict[str, Any]) -> str:
    """The text ingest embedded for this row: a summary row's summary; otherwise the chunk
    plus its stored synthetic questions (rag_storage.ingest / append_to_document)."""
    text = row.get("text") or ""
    if row.get("chunk_index") == -1 and text.startswith("[SUMMARY] "):
        return text[len("[SUMMARY] "):]
    q = (row.get("synthetic_questions") or "").strip()
    return f"{text}\n\nQuestions this answers:\n{q}" if q else text


# ── status ───────────────────────────────────────────────────────────────────


def status() -> Dict[str, Any]:
    from config import settings

    led = _read(LEDGER)
    target = led.get("target")
    live_tables = _tables(_live_dir())
    done = [t for t, v in (led.get("tables") or {}).items() if v.get("done")]
    hist = _read(HISTORY)
    prev = hist.get("previous_model")
    prev_dir = (_live_dir().with_name(f"{_live_dir().name}@{slug(prev)}") if prev else None)
    return {
        "live_model": settings.embedding_model,
        "target": target,
        "running": bool(_task and not _task.done()),
        "stage": led.get("stage"),
        "error": led.get("error"),
        "tables_total": len(live_tables),
        "tables_done": len(done),
        "rows_done": int(led.get("rows_done") or 0),
        "ready": bool(target) and led.get("stage") == "ready",
        "switch_pending": (_data_dir() / FLIP).exists(),
        "rollback_pending": (_data_dir() / ROLLBACK).exists(),
        "previous": ({"model": prev, "bytes": _dir_bytes(prev_dir), "switched_at": hist.get("switched_at")}
                     if prev and prev_dir and prev_dir.is_dir() else None),
        "index_bytes": _dir_bytes(_live_dir()),
    }


def preflight(target: str) -> Dict[str, Any]:
    """Everything that must hold before a build starts (S10 disk, S11 model present)."""
    from config import settings
    from services.model_presence import is_present

    checks: Dict[str, Dict[str, Any]] = {}
    checks["different_model"] = {"ok": bool(target) and target != settings.embedding_model}
    checks["downloaded"] = {"ok": bool(target) and is_present(target)}
    need = _dir_bytes(_live_dir()) * 2 + 512 * 1024 * 1024
    free = shutil.disk_usage(_live_dir().parent).free
    checks["disk"] = {"ok": free >= need, "free_bytes": free, "needed_bytes": need}
    checks["not_running"] = {"ok": not (_task and not _task.done())}
    checks["no_switch_pending"] = {"ok": not (_data_dir() / FLIP).exists() and not (_data_dir() / ROLLBACK).exists()}
    return {"ready": all(c["ok"] for c in checks.values()), "checks": checks}


# ── the build ────────────────────────────────────────────────────────────────


async def start(target: str) -> Dict[str, Any]:
    global _task, _stop
    pre = preflight(target)
    if not pre["ready"]:
        return {"started": False, "preflight": pre}
    led = _read(LEDGER)
    if led.get("target") != target:            # a different target starts over
        led = {"target": target, "tables": {}, "rows_done": 0,
               "started_at": datetime.now(timezone.utc).isoformat()}
    led.update(stage="building", error=None)
    _write(LEDGER, led)
    _stop = False
    from utils.tasks import safe_create_task
    _task = safe_create_task(_run(target), name="embedder-migration")
    return {"started": True, "status": status()}


def stop() -> bool:
    global _stop
    _stop = True
    return bool(_task and not _task.done())


async def _pace() -> None:
    try:
        from services import presence
        await asyncio.sleep(presence.background_pace_seconds())
    except Exception:
        await asyncio.sleep(0.5)


async def _embed(target: str, texts: List[str]) -> List[List[float]]:
    from services import embedding_profile
    from services.llm_runtime import llm_runtime

    prof = embedding_profile.for_model(target)
    return await llm_runtime.embed_with_model([prof.format_document(t) for t in texts], target)


def _table_version(db, name: str) -> Optional[int]:
    try:
        return int(db.open_table(name).version)
    except Exception:
        return None


async def _copy_table(target: str, live_db, shadow_db, name: str, led: Dict[str, Any]) -> int:
    import numpy as np
    import pyarrow as pa

    version = _table_version(live_db, name)
    src = await asyncio.to_thread(lambda: live_db.open_table(name).to_arrow())
    rows = src.drop(["vector"]).to_pylist() if "vector" in src.column_names else src.to_pylist()
    vecs: List[List[float]] = []
    for i in range(0, len(rows), BATCH):
        if _stop:
            raise asyncio.CancelledError("stopped")
        vecs += await _embed(target, [embedded_text(r) for r in rows[i:i + BATCH]])
        led["rows_done"] = int(led.get("rows_done") or 0) + len(rows[i:i + BATCH])
        _write(LEDGER, led)
        await _pace()
    dim = len(vecs[0]) if vecs else 0
    if rows and dim:
        flat = np.asarray(vecs, dtype=np.float32).reshape(-1)
        vec_arr = pa.FixedSizeListArray.from_arrays(pa.array(flat, type=pa.float32()), dim)
        out = src.drop(["vector"]).append_column(pa.field("vector", pa.list_(pa.float32(), dim)), vec_arr)
        await asyncio.to_thread(lambda: shadow_db.create_table(name, data=out, mode="overwrite"))
    elif not rows:
        await asyncio.to_thread(lambda: shadow_db.create_table(name, schema=src.schema, mode="overwrite"))
    led["tables"][name] = {"done": True, "version": version, "rows": len(rows)}
    _write(LEDGER, led)
    return len(rows)


async def _run(target: str) -> None:
    import lancedb

    from services.mlx_engine import mlx_engine
    from services.model_sizing import exact_weight_gb

    led = _read(LEDGER)
    try:
        need = (exact_weight_gb(target) or 1.5) * 1.3
        if not await mlx_engine.admit(target, need_gb=need, wait_s=120.0):
            raise RuntimeError("not enough GPU memory for the new model right now — it will retry")
        live_db = lancedb.connect(str(_live_dir()))
        shadow = shadow_dir(target)
        shadow.mkdir(parents=True, exist_ok=True)
        shadow_db = lancedb.connect(str(shadow))
        # Pass 1: every table not yet copied. Pass 2 (catch-up): every table that changed
        # since it was copied — repeated until a pass finds nothing (S8).
        for _ in range(5):
            names = _tables(_live_dir())
            todo = [n for n in names
                    if not (led["tables"].get(n) or {}).get("done")
                    or (led["tables"][n].get("version") != _table_version(live_db, n))]
            gone = [n for n in _tables(shadow) if n not in names]
            for n in gone:                                   # a notebook deleted meanwhile
                await asyncio.to_thread(shadow_db.drop_table, n)
                led["tables"].pop(n, None)
            if not todo:
                break
            for n in todo:
                led["stage"] = f"copying {n}"
                _write(LEDGER, led)
                await _copy_table(target, live_db, shadow_db, n, led)
        led.update(stage="ready", finished_at=datetime.now(timezone.utc).isoformat())
        _write(LEDGER, led)
        logger.info("[embedder-migration] shadow index for %s ready (%s rows)", target, led.get("rows_done"))
    except asyncio.CancelledError:
        led.update(stage="stopped")
        _write(LEDGER, led)
    except Exception as exc:
        logger.warning("[embedder-migration] build failed: %s", exc)
        led.update(stage="failed", error=str(exc)[:300])
        _write(LEDGER, led)
    finally:
        from config import settings
        if target != settings.embedding_model:
            try:
                await mlx_engine.unload(target)
            except Exception:
                pass


# ── switch / roll back (applied at the next launch) ──────────────────────────


async def request_switch() -> Dict[str, Any]:
    """After a final catch-up, ask for the switch on the next launch."""
    led = _read(LEDGER)
    if led.get("stage") != "ready":
        return {"ok": False, "reason": "the new index is not ready yet"}
    import lancedb
    live_db = lancedb.connect(str(_live_dir()))
    stale = [n for n in _tables(_live_dir())
             if (led["tables"].get(n) or {}).get("version") != _table_version(live_db, n)]
    if stale:                                  # something changed since — copy it first
        await start(led["target"])
        return {"ok": False, "reason": "catching up on recent changes — try again in a moment",
                "catching_up": stale}
    from config import settings
    _write(FLIP, {"target": led["target"], "previous": settings.embedding_model,
                  "requested_at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "restart_required": True}


def request_rollback() -> Dict[str, Any]:
    hist = _read(HISTORY)
    prev = hist.get("previous_model")
    if not prev or not _live_dir().with_name(f"{_live_dir().name}@{slug(prev)}").is_dir():
        return {"ok": False, "reason": "no previous index is kept"}
    _write(ROLLBACK, {"to": prev, "requested_at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "restart_required": True}


def cancel_pending() -> None:
    _drop(FLIP)
    _drop(ROLLBACK)


def remove_previous() -> Dict[str, Any]:
    hist = _read(HISTORY)
    prev = hist.get("previous_model")
    if not prev:
        return {"ok": False, "reason": "nothing to remove"}
    p = _live_dir().with_name(f"{_live_dir().name}@{slug(prev)}")
    freed = _dir_bytes(p)
    shutil.rmtree(p, ignore_errors=True)
    hist["previous_removed_at"] = datetime.now(timezone.utc).isoformat()
    _write(HISTORY, hist)
    return {"ok": True, "freed_bytes": freed}


def _set_saved_embedder(model: str) -> None:
    """The saved choice is re-applied at every launch and wins over config, so it is what
    must change (both keys the combo has carried)."""
    p = _data_dir() / "user_preferences.json"
    try:
        prefs = json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        prefs = {}
    combo = prefs.setdefault("default_combo", {})
    combo["embedding_model"] = model
    combo["embeddings"] = model
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prefs, indent=2))
    os.replace(tmp, p)


def _swap(live: Path, keep_as: Path, incoming: Path) -> None:
    """live → keep_as, incoming → live; undone if the second step fails."""
    if keep_as.exists():
        raise RuntimeError(f"{keep_as.name} already exists — refusing to overwrite it")
    os.rename(live, keep_as)
    try:
        os.rename(incoming, live)
    except Exception:
        os.rename(keep_as, live)
        raise


def apply_pending_at_startup() -> Optional[Dict[str, Any]]:
    """Run once at launch, before anything opens LanceDB or applies the saved models.
    Never raises; a failed step leaves the live index exactly as it was."""
    try:
        live = _live_dir()
        flip, back = _read(FLIP), _read(ROLLBACK)
        if flip:
            target, prev = flip["target"], flip["previous"]
            shadow = shadow_dir(target)
            if not shadow.is_dir():
                raise RuntimeError(f"the new index {shadow.name} is missing")
            _swap(live, live.with_name(f"{live.name}@{slug(prev)}"), shadow)
            _set_saved_embedder(target)
            _write(HISTORY, {"previous_model": prev, "model": target,
                             "switched_at": datetime.now(timezone.utc).isoformat()})
            _drop(FLIP)
            _drop(LEDGER)
            return {"switched": True, "from": prev, "to": target}
        if back:
            hist = _read(HISTORY)
            to, cur = back["to"], hist.get("model")
            kept = live.with_name(f"{live.name}@{slug(to)}")
            if not kept.is_dir():
                raise RuntimeError(f"the previous index {kept.name} is missing")
            _swap(live, live.with_name(f"{live.name}@{slug(cur)}"), kept)
            _set_saved_embedder(to)
            _write(HISTORY, {"previous_model": cur, "model": to,
                             "switched_at": datetime.now(timezone.utc).isoformat(), "rolled_back": True})
            _drop(ROLLBACK)
            return {"switched": True, "from": cur, "to": to, "rollback": True}
    except Exception as exc:
        logger.error("[embedder-migration] switch at startup failed, nothing changed: %s", exc)
        _drop(FLIP)
        _drop(ROLLBACK)
        return {"switched": False, "error": str(exc)}
    return None
