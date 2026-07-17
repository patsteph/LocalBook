"""MLX model download manager (Wave 9.6).

When a user selects an MLX model that isn't on disk yet, the Locker triggers an
immediate download instead of the silent lazy first-use fetch (user #3). This
manager runs `snapshot_download` in a thread and exposes byte-level progress by
polling the HF cache's `blobs/` dir against the total size from the HF API.

Progress is best-effort: if the size probe fails, `pct` is None and the UI shows
an indeterminate bar. Nothing here ever raises to the caller.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _cache_dir_for(model_id: str) -> str:
    from huggingface_hub.constants import HF_HUB_CACHE
    return os.path.join(HF_HUB_CACHE, "models--" + model_id.replace("/", "--"))


def _downloaded_bytes(model_id: str) -> int:
    """Sum the real blob files (incl. *.incomplete) — the actual bytes on disk so far.
    We sum blobs/ only (snapshots/ are symlinks to blobs; counting both double-counts)."""
    blobs = os.path.join(_cache_dir_for(model_id), "blobs")
    if not os.path.isdir(blobs):
        return 0
    total = 0
    for root, _dirs, files in os.walk(blobs):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _is_installed(model_id: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
        return try_to_load_from_cache(model_id, "config.json") is not None
    except Exception:
        return False


class _MLXDownloadManager:
    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}   # model_id -> {status,total_bytes,error,pct}
        self._lock = threading.Lock()

    def status(self, model_id: str) -> Dict[str, Any]:
        with self._lock:
            st = dict(self._state.get(model_id) or {})
        installed = _is_installed(model_id)
        if not st:
            # Never started this session: report from the cache's own truth.
            return {"status": "done" if installed else "idle",
                    "pct": 100 if installed else None,
                    "downloaded_gb": 0.0, "total_gb": 0.0, "error": None}
        status = st.get("status")
        total = st.get("total_bytes") or 0
        downloaded = _downloaded_bytes(model_id)
        if status == "done":
            pct = 100
        elif status == "downloading" and total:
            pct = int(min(99, downloaded / total * 100))
        else:
            pct = st.get("pct")
        return {"status": status, "pct": pct,
                "downloaded_gb": round(downloaded / (1024 ** 3), 2),
                "total_gb": round(total / (1024 ** 3), 2) if total else 0.0,
                "error": st.get("error")}

    async def start(self, model_id: str) -> Dict[str, Any]:
        if not model_id:
            return {"status": "error", "error": "empty model_id"}
        if _is_installed(model_id):
            return {"status": "done", "pct": 100}
        with self._lock:
            cur = self._state.get(model_id)
            if cur and cur.get("status") == "downloading":
                return {"status": "downloading"}
            self._state[model_id] = {"status": "downloading", "total_bytes": 0, "error": None, "pct": 0}

        # Total size for the progress bar (best-effort — indeterminate if it fails).
        total = 0
        try:
            from huggingface_hub import HfApi
            info = HfApi().model_info(model_id, files_metadata=True)
            total = sum((getattr(s, "size", 0) or 0) for s in (info.siblings or []))
        except Exception as e:
            logger.debug(f"[mlx-download] size probe failed for {model_id}: {e}")
        with self._lock:
            if model_id in self._state:
                self._state[model_id]["total_bytes"] = total

        def _run() -> None:
            try:
                from huggingface_hub import snapshot_download
                snapshot_download(model_id)
                with self._lock:
                    self._state[model_id] = {"status": "done", "total_bytes": total,
                                             "error": None, "pct": 100}
                logger.info(f"[mlx-download] {model_id} complete ({round(total/(1024**3),2)} GB)")
            except Exception as e:
                with self._lock:
                    self._state[model_id] = {"status": "error", "total_bytes": total,
                                             "error": str(e)[:300], "pct": None}
                logger.warning(f"[mlx-download] {model_id} failed: {e}")

        asyncio.get_running_loop().run_in_executor(None, _run)
        return {"status": "downloading",
                "total_gb": round(total / (1024 ** 3), 2) if total else 0.0}


mlx_download_manager = _MLXDownloadManager()
