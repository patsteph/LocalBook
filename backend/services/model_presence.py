"""Model presence — what is actually on disk, without asking Ollama.

Stage 3.3. `model_registry.refresh_installed_status` asks Ollama's `/api/tags` what is
installed. With Ollama gone that returns nothing, so `GET /evaluator/models` is empty and the
Evaluator has nothing to run — one of the four blocker-class gaps.

MLX models live in the HuggingFace cache, so presence is a filesystem question. `scan_cache_dir()`
(huggingface_hub 1.23.0, present) enumerates it without a network call, which also means this
works offline and on a machine that has never reached huggingface.co.

Pure reads — `stat()` and a cache walk, no downloads, no model loads. Cached because the Locker
re-renders often and a cache scan is not free.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {}
_TTL_S = 30.0


def engine_ok() -> bool:
    """Is the MLX engine importable at all? (No model load.)"""
    try:
        import importlib.util
        return all(importlib.util.find_spec(m) is not None
                   for m in ("mlx", "mlx_lm", "mlx_vlm"))
    except Exception:
        return False


def is_present(model_id: str) -> bool:
    """Is this HF model fully in the local cache? Never touches the network."""
    if not model_id:
        return False
    try:
        from services.model_sizing import exact_weight_gb
        # exact_weight_gb only returns a number when the weights are actually on disk, so it
        # doubles as a presence check that cannot be fooled by a half-finished download of
        # config files alone.
        return exact_weight_gb(model_id) is not None
    except Exception:
        return False


def enumerate_cached(force: bool = False) -> List[Dict[str, Any]]:
    """Every MLX-looking model in the HF cache, with its real size.

    Filtered to repos that actually carry weights — a cache entry can exist for a tokenizer or
    a dataset, and listing those as installable models would be a lie the Locker cannot act on.
    """
    hit = _CACHE.get("enum")
    if hit and not force and (time.time() - hit[0]) < _TTL_S:
        return hit[1]

    out: List[Dict[str, Any]] = []
    try:
        from huggingface_hub import scan_cache_dir
        from services.model_sizing import exact_weight_gb, load_config

        info = scan_cache_dir()
        for repo in info.repos:
            if getattr(repo, "repo_type", "model") != "model":
                continue
            rid = repo.repo_id
            w = exact_weight_gb(rid)
            if w is None:
                continue                      # no weights on disk → not runnable
            cfg = load_config(rid) or {}
            t = cfg.get("text_config") or cfg
            out.append({
                "model_id": rid,
                "weight_gb": w,
                "size_on_disk_gb": round(repo.size_on_disk / 1024 ** 3, 3),
                "family": (cfg.get("model_type") or t.get("model_type") or ""),
                "native_ctx": t.get("max_position_embeddings") or cfg.get("max_position_embeddings"),
                "quantization": (cfg.get("quantization") or {}).get("bits"),
                "vision": bool(cfg.get("vision_config") or cfg.get("image_token_index")),
            })
    except Exception as e:
        logger.warning(f"[model-presence] cache scan failed: {e}")
        return []

    out.sort(key=lambda m: m["model_id"])
    _CACHE["enum"] = (time.time(), out)
    return out


def readiness(roles: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Which configured roles can actually run right now.

    `blocking` is the honest answer to "can the app work" — a role pointed at a model that was
    never downloaded will fail at first use, and saying so up front beats a multi-GB stall
    inside a request.
    """
    if roles is None:
        try:
            from config import settings
            roles = {
                "main": getattr(settings, "mlx_main_model", ""),
                "fast": getattr(settings, "mlx_fast_model", ""),
                "vision": getattr(settings, "mlx_vision_model", ""),
                "embed": getattr(settings, "mlx_embedding_model", ""),
            }
        except Exception:
            roles = {}
    present = {r: (bool(m) and is_present(m)) for r, m in (roles or {}).items()}
    # vision is not blocking: the app degrades to text-only rather than failing.
    blocking = [r for r, ok in present.items() if not ok and r in ("main", "fast", "embed")]
    return {
        "engine_ok": engine_ok(),
        "roles": {r: {"model": (roles or {}).get(r, ""), "present": ok}
                  for r, ok in present.items()},
        "blocking": blocking,
        "ready": engine_ok() and not blocking,
    }


def reset_cache() -> None:
    _CACHE.clear()
