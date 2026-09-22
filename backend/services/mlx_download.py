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
from typing import Any, Dict, List

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

    def active(self) -> Dict[str, Dict[str, Any]]:
        """Every model this session has a tracked download state for, keyed by model id, with
        computed progress. Lets the UI render download chips without knowing the ids in advance —
        specifically the klein (image) / arctic (embeddings) downloads the Locker auto-starts on
        all-MLX adoption, which never appear as pickable model cards. (Snapshot the keys under the
        lock, then release before calling status() per id — status() re-locks and the lock is not
        reentrant.)"""
        with self._lock:
            ids = list(self._state.keys())
        return {mid: self.status(mid) for mid in ids}

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

        # `hf_transport` says to call this before ANY snapshot_download, and this path never
        # did — so the SSL bypass and the Xet limits it configures reached every download route
        # except the one the model browser actually uses. Idempotent.
        try:
            from services.hf_transport import install_hf_transport
            install_hf_transport()
        except Exception:
            pass

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


# ── removing a model ────────────────────────────────────────────────────────
#
# Testing a model means sometimes deciding against it, and a 16 GB checkpoint
# that lost a bake-off should not have to be hunted down in ~/.cache by hand.
#
# The whole risk here is deleting something the app is standing on. Two guards,
# and the first is not negotiable: a model assigned to a role is refused
# outright, because removing it leaves the app pointing at weights that no
# longer exist — and the failure would surface later, as a broken chat rather
# than as a refused deletion.

_ROLE_SETTINGS = ("main_model", "fast_model", "vision_model",
                  "embedding_model", "image_model")


def roles_using(model_id: str) -> List[str]:
    """Which role slots point at this checkpoint. Empty means safe to remove."""
    try:
        from config import settings
        # Read every slot explicitly. `getattr(None, "main_model", "")` returns
        # the default rather than raising, so a broken settings object would
        # have looked like "no role uses this" — the unsafe answer — instead of
        # reaching the guard below.
        assigned = {}
        for r in _ROLE_SETTINGS:
            assigned[r] = getattr(settings, r)      # raises if settings is absent
        return [r.replace("_model", "") for r in _ROLE_SETTINGS
                if (assigned[r] or "") == model_id]
    except Exception:
        # Cannot tell → claim it IS in use. Refusing a safe deletion costs a
        # click; allowing an unsafe one costs the running app.
        return ["unknown"]


def cached_size_gb(model_id: str) -> float:
    """Bytes this model occupies, as GiB. 0.0 when it is not cached."""
    try:
        from services.model_sizing import exact_weight_gb
        return float(exact_weight_gb(model_id) or 0.0)
    except Exception:
        return 0.0


async def delete_model(model_id: str, *, force: bool = False) -> Dict[str, Any]:
    """Remove a downloaded model from the HuggingFace cache.

    Unloads it from the engine first: deleting files out from under a resident
    model leaves the process holding mappings into a file that no longer exists.
    """
    if not model_id:
        return {"ok": False, "error": "No model specified."}

    in_use = roles_using(model_id)
    if in_use and not force:
        names = ", ".join(in_use)
        return {"ok": False, "in_use_by": in_use,
                "error": f"{model_id} is assigned to the {names} role"
                         f"{'s' if len(in_use) > 1 else ''}. Pick a different model "
                         f"for {'them' if len(in_use) > 1 else 'it'} first."}

    freed = cached_size_gb(model_id)

    # Release it before touching the files.
    try:
        from services.mlx_engine import mlx_engine
        await mlx_engine.unload(model_id)
    except Exception as e:
        logger.debug(f"[mlx-download] unload before delete skipped: {e}")

    try:
        from huggingface_hub import scan_cache_dir
        cache = scan_cache_dir()
        repo = next((r for r in cache.repos if r.repo_id == model_id), None)
        if repo is None:
            return {"ok": False, "error": f"{model_id} is not in the local cache."}
        # Delete every revision of this repo — a strategy object, so the bytes
        # are only removed once execute() runs.
        strategy = cache.delete_revisions(*[rev.commit_hash for rev in repo.revisions])
        freed_exact = strategy.expected_freed_size / 1024 ** 3
        strategy.execute()
    except Exception as e:
        logger.warning(f"[mlx-download] delete failed for {model_id}: {e}")
        return {"ok": False, "error": f"Could not remove {model_id}: {e}"}

    # Verify it actually went, rather than trusting the call.
    try:
        from services.model_sizing import reset_cache
        reset_cache()
    except Exception:
        pass
    still_there = cached_size_gb(model_id) > 0
    if still_there:
        return {"ok": False,
                "error": f"{model_id} still appears in the cache after removal."}

    logger.info(f"[mlx-download] removed {model_id}, freed {freed_exact:.2f} GB")
    return {"ok": True, "model_id": model_id,
            "freed_gb": round(freed_exact or freed, 2)}
