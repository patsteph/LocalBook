"""Settings API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import json
import threading
from config import settings
from services.keychain_manager import (
    get_api_key as _km_get,
    get_api_key_async as _km_get_async,
    set_api_key as _km_set,
    delete_api_key as _km_delete,
    get_all_keys_status as _km_status,
)

router = APIRouter()

# Module-level cache for /ollama/models with a lock for thread safety
_ollama_models_cache: dict = {"ts": None, "data": None}
_ollama_models_lock = threading.Lock()


# ─── MLX model card helpers (Wave 9.6) ───────────────────────────────────────
from utils.model_display import friendly_model_name  # shared: friendly names EVERYWHERE


def _mlx_cache_size_gb(mid: str, installed: bool) -> float:
    """Real on-disk size of the HF snapshot (config + weights + tokenizer), summed via
    the blob symlinks. 0.0 when not installed (the card then shows an estimate)."""
    if not installed:
        return 0.0
    try:
        import os
        from huggingface_hub import try_to_load_from_cache
        p = try_to_load_from_cache(mid, "config.json")
        if not p or not isinstance(p, str):
            return 0.0
        snap = os.path.dirname(p)
        total = 0
        for root, _dirs, files in os.walk(snap):
            for f in files:
                try:
                    total += os.path.getsize(os.path.realpath(os.path.join(root, f)))
                except OSError:
                    pass
        return round(total / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _mlx_estimate_size_gb(param_count_b: float, quantization: str) -> float:
    """Rough download/disk estimate when a model isn't installed yet, from param count
    + quant (4bit≈0.5 B/param, 8bit≈1, bf16/fp16≈2) with ~10% metadata overhead."""
    if not param_count_b or param_count_b <= 0:
        return 0.0
    q = (quantization or "").lower()
    bpp = 0.5 if "4" in q else 1.0 if "8" in q else 2.0
    return round(param_count_b * bpp * 1.1, 1)


class MLXDownloadRequest(BaseModel):
    model_id: str


@router.post("/mlx/download")
async def mlx_download_start(req: MLXDownloadRequest):
    """Start (or no-op if running/installed) an MLX model download so selecting a
    not-yet-downloaded MLX model fetches it immediately with progress, instead of the
    silent lazy first-use pull (user #3)."""
    from services.mlx_download import mlx_download_manager
    return await mlx_download_manager.start(req.model_id)


@router.get("/mlx/download-status")
async def mlx_download_status(model_id: str):
    """Poll target for the MLX download progress bar: {status, pct, downloaded_gb, total_gb}."""
    from services.mlx_download import mlx_download_manager
    return mlx_download_manager.status(model_id)


@router.get("/mlx/downloads")
async def mlx_downloads_active():
    """All MLX downloads tracked this session, keyed by model id — for the background-download
    progress chips. Covers the klein (image) / arctic (embeddings) downloads the Locker auto-starts
    on all-MLX adoption, which have no pickable model card of their own."""
    from services.mlx_download import mlx_download_manager
    return mlx_download_manager.active()

# User profile storage path
USER_PROFILE_PATH = settings.data_dir / "user_profile.json"

# App preferences storage path
APP_PREFERENCES_PATH = settings.data_dir / "app_preferences.json"

class SetAPIKeyRequest(BaseModel):
    key_name: str
    value: str

class UserProfile(BaseModel):
    """User profile for personalization"""
    name: Optional[str] = None
    profession: Optional[str] = None
    expertise_level: Optional[str] = None  # beginner, intermediate, expert
    response_style: Optional[str] = None  # concise, detailed, balanced
    tone: Optional[str] = None  # formal, casual, professional
    interests: Optional[List[str]] = None
    favorite_authors: Optional[List[str]] = None
    favorite_topics: Optional[List[str]] = None
    goals: Optional[str] = None
    custom_instructions: Optional[str] = None

class AppPreferences(BaseModel):
    """App-wide preferences"""
    primary_notebook_id: Optional[str] = None

class APIKeysStatusResponse(BaseModel):
    configured: dict[str, bool]

@router.get("/api-keys/status", response_model=APIKeysStatusResponse)
async def get_api_keys_status():
    """Get the status of all API keys (configured or not)"""
    key_names = [
        "brave_api_key",
        "youtube_api_key",
        "anthropic_api_key",
        "openai_api_key",
        "gemini_api_key",
        "custom_llm",
    ]
    try:
        configured = await _km_status(key_names)
    except Exception:
        configured = {k: False for k in key_names}
    return APIKeysStatusResponse(configured=configured)

@router.post("/api-keys/set")
async def set_api_key(request: SetAPIKeyRequest):
    """Set an API key in the system keychain"""
    try:
        _km_set(request.key_name, request.value)
        return {"message": f"API key '{request.key_name}' saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save API key: {str(e)}")

@router.delete("/api-keys/{key_name}")
async def delete_api_key(key_name: str):
    """Delete an API key from the system keychain"""
    try:
        _km_delete(key_name)
        return {"message": f"API key '{key_name}' deleted successfully"}
    except KeyError:
        return {"message": f"API key '{key_name}' was not configured"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {str(e)}")

@router.get("/ollama/models")
async def get_ollama_models():
    """
    Return all locally installed Ollama models enriched with live metadata.

    Calls GET /api/tags (model list) then POST /api/show (per-model details)
    in parallel, classifies each model into main / fast / vision / embeddings,
    and appends current-active state from settings.  Cached in-process for 30 s.
    """
    import asyncio
    import time
    import httpx
    from config import settings as app_settings

    CACHE_TTL = 30  # seconds

    # Module-level cache — checked under lock to prevent concurrent fetches
    now = time.monotonic()
    with _ollama_models_lock:
        if _ollama_models_cache["ts"] and (now - _ollama_models_cache["ts"]) < CACHE_TTL:
            return _ollama_models_cache["data"]

    base_url = app_settings.ollama_base_url

    async def _fetch_tags(client: httpx.AsyncClient) -> list:
        try:
            r = await client.get(f"{base_url}/api/tags", timeout=5.0)
            r.raise_for_status()
            return r.json().get("models", [])
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Ollama not reachable: {e}")

    async def _fetch_show(client: httpx.AsyncClient, name: str) -> dict:
        try:
            r = await client.post(
                f"{base_url}/api/show",
                json={"name": name},
                timeout=8.0,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as _e:
            logger.warning(f"[settings] {type(_e).__name__}: {_e}")
        return {}

    def _is_vision_model(name: str, show: dict) -> bool:
        """Detect if a model has vision capabilities from Ollama metadata."""
        lower = name.lower()
        if "vision" in lower or "llava" in lower:
            return True
        mi = show.get("model_info", {})
        return (
            any("projector" in k for k in mi)
            or mi.get("clip.has_vision_encoder", False)
        )

    def _classify_model(name: str, show: dict, size_bytes: int, reg) -> str:
        """
        Classify an installed model into main / fast / vision / embeddings.

        Waterfall (checked in order):
        1. Embedding keyword or architecture       → "embeddings"
        2. Registry: ONLY vision_model (no main/fast) → "vision"
        3. Registry: has main_model or fast_model   → use that role
        4. No registry + has vision + disk < 4 GB   → "vision"
        5. Disk size >= 4 GB                        → "main"
        6. Disk size < 4 GB                         → "fast"
        """
        lower = name.lower()

        # Step 1: Embeddings
        embed_keywords = ("embed", "nomic", "mxbai", "bge", "minilm", "gte-")
        if any(k in lower for k in embed_keywords):
            return "embeddings"
        mi = show.get("model_info", {})
        if mi.get("general.architecture") == "bert":
            return "embeddings"

        # Step 2 & 3: Registry-based classification
        if reg:
            roles = reg.supported_roles or []
            has_main = "main_model" in roles
            has_fast = "fast_model" in roles
            has_vision_only = "vision_model" in roles and not has_main and not has_fast
            if has_vision_only:
                return "vision"
            # If model has both main + fast roles, use whichever is listed first
            # (registry convention: primary role is listed first)
            if has_main and has_fast:
                return "main" if roles.index("main_model") < roles.index("fast_model") else "fast"
            if has_main:
                return "main"
            if has_fast:
                return "fast"

        # Step 4: Non-registry vision model (small) — surface in the Vision column
        # (frontend Role type is main|fast|vision|embeddings; "specialty" silently
        # filters out of every column)
        size_gb = size_bytes / (1024 ** 3)
        if not reg and _is_vision_model(name, show) and size_gb < 4.0:
            return "vision"

        # Step 5 & 6: Disk-size threshold
        if size_gb >= 4.0:
            return "main"
        return "fast"

    def _estimate_ram(size_bytes: int, reg) -> float:
        """Estimate required RAM in GB.
        
        Registry models use hand-verified min_ram_gb.
        Unknown models: disk_size * 1.3 (weights + KV cache + overhead).
        """
        if reg and reg.min_ram_gb > 0:
            return float(reg.min_ram_gb)
        return round(size_bytes / (1024 ** 3) * 1.3, 1)

    def _parse_context(show: dict) -> int:
        """Extract context window from model metadata."""
        params = show.get("model_info", {})
        for key in ("llama.context_length", "context_length"):
            val = params.get(key)
            if val and isinstance(val, int):
                return val
        # Fallback: check modelfile for num_ctx
        modelfile = show.get("modelfile", "")
        for line in modelfile.splitlines():
            if line.strip().upper().startswith("PARAMETER NUM_CTX"):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        return int(parts[2])
                    except ValueError as _e:
                        logger.debug(f"[settings] {type(_e).__name__}: {_e}")
        return 4096

    from evaluator.model_registry import model_registry  # hoisted — one import for all enrichments

    # Pre-load evaluator scores so we can attach best score per model
    _eval_scores: dict[str, float] = {}  # model_name → best overall_score
    try:
        from evaluator.evaluator_service import get_results_list
        for run in get_results_list():
            for key in ("main_model", "fast_model"):
                mname = run.get(key, "")
                score = run.get("overall_score", 0)
                if mname and score > _eval_scores.get(mname, 0):
                    _eval_scores[mname] = score
    except Exception:
        pass  # evaluator may not have any runs yet

    def _extract_quant(name: str, show: dict) -> str:
        """Extract quantization level from model name tag or modelfile FROM line."""
        import re as _re
        # Common quant patterns: Q4_K_M, Q5_0, Q8_0, F16, FP16, BF16, etc.
        _quant_re = _re.compile(r'(Q\d+_K(?:_[A-Z])?|Q\d+_[0-9]|(?:B?F|FP)16|F32)', _re.IGNORECASE)

        # 1. Check model name/tag (e.g., "model:7b-q4_K_M")
        qmatch = _quant_re.search(name)
        if qmatch:
            return qmatch.group(1).upper()

        # 2. Check modelfile FROM line which contains the GGUF blob/filename
        modelfile = show.get("modelfile", "")
        for line in modelfile.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("FROM"):
                qmatch = _quant_re.search(stripped)
                if qmatch:
                    return qmatch.group(1).upper()

        return ""

    def _extract_param_count(name: str, show: dict) -> str:
        """Extract parameter count string from Ollama metadata or name."""
        mi = show.get("model_info", {})
        meta_params = mi.get("general.parameter_count")
        if meta_params and isinstance(meta_params, (int, float)):
            if meta_params >= 1_000_000_000:
                return f"{meta_params / 1_000_000_000:.1f}B".replace(".0B", "B")
            elif meta_params >= 1_000_000:
                return f"{meta_params / 1_000_000:.0f}M"
        # Fallback: extract from name (e.g., "7b", "3.8b")
        import re as _re
        m = _re.search(r'(\d+(?:\.\d+)?)\s*[bB]', name)
        return f"{m.group(1)}B" if m else ""

    async with httpx.AsyncClient() as client:
        raw_models = await _fetch_tags(client)

        semaphore = asyncio.Semaphore(4)

        async def _enrich(m: dict) -> dict:
            async with semaphore:
                name = m.get("name", "")
                size_bytes = m.get("size", 0)
                show = await _fetch_show(client, name)

                reg = model_registry.get_model(name)

                suggested_role = _classify_model(name, show, size_bytes, reg)
                ram_required = _estimate_ram(size_bytes, reg)
                context_window = _parse_context(show)

                # Vision detection
                if reg:
                    _vision = reg.supports_vision
                else:
                    _vision = _is_vision_model(name, show)

                # also_vision: model should appear in the Vision column in addition to its primary role
                _also_vision = bool(reg and "vision_model" in (reg.supported_roles or []))

                # Parameter count: registry > Ollama metadata > name parse
                param_count = (reg.parameter_count if reg else "") or _extract_param_count(name, show)

                # Build E (2026-07-07): probe-derived capabilities + capability-based
                # role eligibility + RAM-fit on THIS Mac, built from the already-
                # fetched /api/show (no extra call). Lets the Locker UI show a model
                # in EVERY column it's eligible for (not one size-based column) +
                # capability badges + a fits/tight/over chip.
                _caps_flags = {"vision": _vision, "embedding": False,
                               "tools": False, "thinking": False, "audio": False}
                _supported_roles = list(reg.supported_roles) if (reg and reg.supported_roles) else []
                _ram_fit = None
                try:
                    from evaluator.capability_probe import OllamaCapabilityProbe
                    from evaluator import ram_fit as _ramfit
                    from evaluator.hardware_profiler import get_hardware_profile as _ghp
                    _pc = OllamaCapabilityProbe.from_show(name, show)
                    _caps_flags = {"vision": _vision or _pc.vision, "embedding": _pc.embedding,
                                   "tools": _pc.tools, "thinking": _pc.thinking, "audio": _pc.audio}
                    for _r in _pc.roles():
                        if _r not in _supported_roles:
                            _supported_roles.append(_r)
                    _total_ram = float(getattr(_ghp(), "memory_gb", 0) or 0)
                    if _total_ram > 0 and _pc.param_count_b > 0:
                        # Fit against the DEPLOYED window (RAM-scaled effective cap),
                        # not the model's native ceiling — the app never runs 131k on
                        # a 16GB Mac, so a native-ctx KV estimate would falsely say
                        # "over" for every large-window model.
                        try:
                            from services.ollama_service import effective_num_ctx_cap
                            _deployed_ctx = effective_num_ctx_cap(name) or 8192
                        except Exception:
                            _deployed_ctx = 8192
                        _f = _ramfit.ram_fit(_pc.param_count_b, _pc.quantization, _total_ram, _deployed_ctx)
                        # Surface the FULL breakdown (not just fits/weight/budget) so
                        # a "too big" verdict is explainable in the UI/tooltip and
                        # diagnosable off-machine — kv + total_needed + deployed ctx
                        # are what actually decide the recommendation.
                        _ram_fit = {"fits": _f["fits"], "recommendation": _f["recommendation"],
                                    "weight_gb": _f["weight_gb"], "kv_gb": _f["kv_gb"],
                                    "budget_gb": _f["budget_gb"], "total_needed_gb": _f["total_needed_gb"],
                                    "headroom_gb": _f["headroom_gb"], "deployed_ctx": _f["context_tokens"],
                                    "total_ram_gb": _total_ram}
                except Exception as _ce:
                    logger.debug(f"[settings] capability enrichment failed for {name}: {_ce}")

                return {
                    "name": name,
                    "display_name": (reg.display_name if reg else name.split(":")[0].replace("-", " ").title()),
                    "family": (reg.family if reg else ""),
                    "size_bytes": size_bytes,
                    "size_gb": round(size_bytes / (1024 ** 3), 1),
                    "ram_required_gb": ram_required,
                    "context_window": context_window,
                    "suggested_role": suggested_role,
                    "supported_roles": _supported_roles,
                    "capabilities": _caps_flags,
                    "ram_fit": _ram_fit,
                    "supports_vision": _caps_flags["vision"],
                    "also_vision": _also_vision,
                    "supports_json_mode": (reg.supports_json_mode if reg else False),
                    "vendor": (reg.vendor if reg else "Community"),
                    "origin_country": (reg.origin_country if reg else ""),
                    "parameter_count": param_count,
                    "quantization": _extract_quant(name, show),
                    "eval_score": _eval_scores.get(name, 0),
                    "modified_at": m.get("modified_at", ""),
                    "in_registry": reg is not None,
                    "provider": (getattr(reg, "provider", "ollama") if reg else "ollama"),
                }

        enriched = await asyncio.gather(*[_enrich(m) for m in raw_models])

        # v1.7.0: append llama-server sidecar models when the sidecar is healthy.
        # These are registry-only (not in Ollama's /api/tags) so they need to be
        # surfaced separately for the UI to display them.
        try:
            from services.llm_provider import health_check, Provider as _Provider
            sidecar_healthy = await health_check(_Provider.LLAMA_SERVER)
        except Exception:
            sidecar_healthy = False

        if sidecar_healthy:
            for reg_model in model_registry.list_all():
                if getattr(reg_model, "provider", "ollama") != "llama_server":
                    continue
                # Guess suggested_role from registry metadata
                roles = reg_model.supported_roles or []
                if "main_model" in roles:
                    _sr = "main"
                elif "fast_model" in roles:
                    _sr = "fast"
                else:
                    _sr = "main"
                enriched.append({
                    "name": reg_model.ollama_name,
                    "display_name": reg_model.display_name,
                    "family": reg_model.family,
                    "size_bytes": int(reg_model.disk_size_gb * (1024 ** 3)),
                    "size_gb": reg_model.disk_size_gb,
                    "ram_required_gb": float(reg_model.min_ram_gb),
                    "context_window": reg_model.context_window,
                    "suggested_role": _sr,
                    "supports_vision": reg_model.supports_vision,
                    "also_vision": "vision_model" in (reg_model.supported_roles or []),
                    "supports_json_mode": reg_model.supports_json_mode,
                    "vendor": reg_model.vendor,
                    "origin_country": reg_model.origin_country,
                    "parameter_count": reg_model.parameter_count,
                    "quantization": "",
                    "eval_score": _eval_scores.get(reg_model.ollama_name, 0),
                    "modified_at": "",
                    "in_registry": True,
                    "provider": "llama_server",
                })

        # Wave 9.4 — append MLX models (opt-in engine). Surface the configured mlx_* models so
        # the Locker can show + select them. Caps via config.json probe (no model load); RAM-fit
        # via ram_fit; installed = HF snapshot cached. Never fatal.
        try:
            from services.mlx_engine import MLXEngine as _MLXEngine
            _mlx_ok = _MLXEngine.available()
        except Exception:
            _mlx_ok = False
        if _mlx_ok:
            try:
                from evaluator.capability_probe import probe_capabilities as _mprobe
                from evaluator import ram_fit as _ramfit_mod
                from huggingface_hub import try_to_load_from_cache as _tlfc
                try:
                    from services.hardware_profiler import get_hardware_profile as _ghp
                    _total_ram_mlx = float(_ghp().memory_gb)
                except Exception:
                    _total_ram_mlx = 0.0
                _seen = {e.get("name") for e in enriched}
                _mlx_ids = dict.fromkeys(
                    getattr(app_settings, k, None)
                    for k in ("mlx_main_model", "mlx_fast_model", "mlx_vision_model",
                              "mlx_embedding_model"))
                for _mid in [x for x in _mlx_ids if x and x not in _seen]:
                    _c = _mprobe(_mid, provider="mlx")
                    if not _c:
                        continue
                    # Constrain each MLX card to the role SLOT it fills in config, not every
                    # role its capabilities allow — so the MLX gemma shows only under Main
                    # (+ Vision) and MLX phi only under Fast, mirroring their Ollama
                    # counterparts instead of flooding both columns (user #2).
                    _role_slots = []
                    if _mid == getattr(app_settings, "mlx_main_model", None):
                        _role_slots.append("main_model")
                    if _mid == getattr(app_settings, "mlx_fast_model", None):
                        _role_slots.append("fast_model")
                    if _mid == getattr(app_settings, "mlx_vision_model", None):
                        _role_slots.append("vision_model")
                    if _mid == getattr(app_settings, "mlx_embedding_model", None):
                        _role_slots.append("embedding_model")
                    _roles = list(dict.fromkeys(_role_slots)) or _c.roles()
                    _sr = ("fast" if _role_slots == ["fast_model"]
                           else "vision" if _role_slots == ["vision_model"]
                           else "embeddings" if _role_slots == ["embedding_model"]
                           else "main")
                    _installed = _tlfc(_mid, "config.json") is not None
                    # Real disk size if downloaded; otherwise an estimate so the card is
                    # never a blank "0 GB" (user #1 — MLX cards must carry the same data).
                    _size_gb = _mlx_cache_size_gb(_mid, _installed) or \
                        _mlx_estimate_size_gb(_c.param_count_b, _c.quantization)
                    _card = {
                        "name": _mid, "display_name": friendly_model_name(_mid),
                        "family": _c.family, "size_gb": _size_gb,
                        "ram_required_gb": round(_size_gb * 1.3, 1) if _size_gb else 0,
                        "context_window": _c.native_ctx, "suggested_role": _sr,
                        "supported_roles": _roles,
                        "capabilities": {"vision": _c.vision, "embedding": _c.embedding,
                                         "thinking": _c.thinking, "tools": False, "audio": False},
                        "supports_vision": _c.vision, "also_vision": _c.vision,
                        "supports_json_mode": True, "vendor": "MLX Community",
                        "origin_country": "", "parameter_count": _c.param_size or f"{_c.param_count_b}B",
                        "quantization": _c.quantization, "provider": "mlx",
                        "installed": _installed,
                        "in_registry": False, "eval_score": 0, "modified_at": "",
                    }
                    if _total_ram_mlx > 0 and _c.param_count_b > 0:
                        try:
                            _f = _ramfit_mod.ram_fit(_c.param_count_b, _c.quantization,
                                                     _total_ram_mlx, _c.native_ctx or 8192)
                            _card["ram_fit"] = {"fits": _f["fits"], "recommendation": _f["recommendation"]}
                            # Prefer the fit calc's total (weights+KV) for the RAM figure.
                            if _f.get("total_needed_gb"):
                                _card["ram_required_gb"] = round(float(_f["total_needed_gb"]), 1)
                        except Exception:
                            pass
                    enriched.append(_card)
            except Exception as _mlx_e:
                logger.debug(f"[settings] MLX model enumeration failed: {_mlx_e}")

    # Attach active-role flags from current settings (engine-aware: mlx role → mlx model)
    def _active_for(engine_attr, mlx_attr, ollama_val):
        return getattr(app_settings, mlx_attr) if getattr(app_settings, engine_attr, "ollama") == "mlx" else ollama_val
    active = {
        "main": _active_for("main_engine", "mlx_main_model", app_settings.ollama_model),
        "fast": _active_for("fast_engine", "mlx_fast_model", app_settings.ollama_fast_model),
        "embeddings": _active_for("embed_engine", "mlx_embedding_model", app_settings.embedding_model),
        "vision": _active_for("vision_engine", "mlx_vision_model", app_settings.vision_model),
    }

    def _names_match(config_name: str, ollama_name: str) -> bool:
        """Config may omit ':latest' tag that Ollama includes."""
        if config_name == ollama_name:
            return True
        # "snowflake-arctic-embed2" matches "snowflake-arctic-embed2:latest"
        if ollama_name.endswith(":latest") and config_name == ollama_name.rsplit(":latest", 1)[0]:
            return True
        # "model" matches "model:tag" by base name
        if config_name == ollama_name.split(":")[0]:
            return True
        return False

    for m in enriched:
        m["active_as"] = next(
            (role for role, active_name in active.items() if _names_match(active_name, m["name"])),
            None,
        )

    result = {"models": list(enriched), "active": active}
    with _ollama_models_lock:
        _ollama_models_cache["data"] = result
        _ollama_models_cache["ts"] = now
    return result


@router.get("/llm-info")
async def get_llm_info():
    """Get current LLM model information"""
    return {
        "model_name": settings.ollama_model,
        "fast_model_name": settings.ollama_fast_model,
        "provider": settings.llm_provider
    }

def get_api_key(key_name: str) -> str | None:
    """Sync helper to get an API key (safe for background tasks / startup)."""
    try:
        return _km_get(key_name)
    except Exception:
        return None


# ==================== User Profile Endpoints ====================

@router.get("/user-profile", response_model=UserProfile)
async def get_user_profile():
    """Get the user profile for personalization"""
    try:
        if USER_PROFILE_PATH.exists():
            with open(USER_PROFILE_PATH, 'r') as f:
                data = json.load(f)
                return UserProfile(**data)
        return UserProfile()
    except Exception as e:
        print(f"Error loading user profile: {e}")
        return UserProfile()


@router.post("/user-profile")
async def save_user_profile(profile: UserProfile):
    """Save the user profile for personalization"""
    try:
        # Ensure data directory exists
        USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        with open(USER_PROFILE_PATH, 'w') as f:
            json.dump(profile.model_dump(exclude_none=True), f, indent=2)
        
        return {"message": "User profile saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save user profile: {str(e)}")


@router.delete("/user-profile")
async def delete_user_profile():
    """Delete the user profile"""
    try:
        if USER_PROFILE_PATH.exists():
            USER_PROFILE_PATH.unlink()
        return {"message": "User profile deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete user profile: {str(e)}")


def get_user_profile_sync() -> dict:
    """Helper function to get user profile synchronously (for use in RAG engine)"""
    try:
        if USER_PROFILE_PATH.exists():
            with open(USER_PROFILE_PATH, 'r') as f:
                return json.load(f)
        return {}
    except Exception:
        return {}


def build_user_context(profile: dict) -> str:
    """Build condensed user context for system prompt (~100 tokens).
    
    IMPORTANT: Personalization should be subtle and natural, not forced into every response.
    The user's name and profession are background context, not something to repeat constantly.
    """
    if not profile:
        return ""
    
    parts = []
    
    # Core instruction: be natural, don't over-personalize
    parts.append("PERSONALIZATION GUIDELINES: Use the user's background context naturally and sparingly. Do NOT start every response with their name or profession. Only reference personal details when directly relevant to the answer. Focus on answering the question first.")
    
    if profile.get('name'):
        parts.append(f"User's name: {profile['name']} (use occasionally, not every response).")
    
    if profile.get('response_style') == 'concise':
        parts.append("Keep responses brief and focused.")
    elif profile.get('response_style') == 'detailed':
        parts.append("Provide thorough, detailed explanations.")
    
    if profile.get('tone') == 'formal':
        parts.append("Use formal, professional language.")
    elif profile.get('tone') == 'casual':
        parts.append("Use casual, friendly language.")
    
    if profile.get('profession'):
        parts.append(f"User's profession: {profile['profession']} (background context, don't mention unless relevant).")
    
    if profile.get('expertise_level') == 'beginner':
        parts.append("Explain concepts simply, avoiding jargon.")
    elif profile.get('expertise_level') == 'expert':
        parts.append("You can use technical terminology freely.")
    
    if profile.get('interests'):
        interests = ', '.join(profile['interests'][:5])
        parts.append(f"User interests (for occasional relevant examples): {interests}.")
    
    if profile.get('goals'):
        parts.append(f"User's goal: {profile['goals']}")
    
    if profile.get('custom_instructions'):
        parts.append(profile['custom_instructions'])
    
    return ' '.join(parts)


# ==================== App Preferences Endpoints ====================

def _load_app_preferences() -> dict:
    """Load app preferences from disk"""
    try:
        if APP_PREFERENCES_PATH.exists():
            with open(APP_PREFERENCES_PATH, 'r') as f:
                return json.load(f)
        return {}
    except Exception:
        return {}


def _save_app_preferences(prefs: dict):
    """Save app preferences to disk"""
    APP_PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(APP_PREFERENCES_PATH, 'w') as f:
        json.dump(prefs, f, indent=2)


@router.get("/preferences", response_model=AppPreferences)
async def get_app_preferences():
    """Get app preferences"""
    data = _load_app_preferences()
    return AppPreferences(**data)


@router.post("/preferences")
async def save_app_preferences(prefs: AppPreferences):
    """Save app preferences"""
    try:
        _save_app_preferences(prefs.model_dump(exclude_none=True))
        return {"message": "Preferences saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/primary-notebook")
async def get_primary_notebook():
    """Get the primary notebook ID"""
    prefs = _load_app_preferences()
    return {"primary_notebook_id": prefs.get("primary_notebook_id")}


@router.post("/primary-notebook/{notebook_id}")
async def set_primary_notebook(notebook_id: str):
    """Set the primary notebook"""
    prefs = _load_app_preferences()
    prefs["primary_notebook_id"] = notebook_id
    _save_app_preferences(prefs)
    return {"message": "Primary notebook set", "primary_notebook_id": notebook_id}


@router.delete("/primary-notebook")
async def clear_primary_notebook():
    """Clear the primary notebook"""
    prefs = _load_app_preferences()
    prefs.pop("primary_notebook_id", None)
    _save_app_preferences(prefs)
    return {"message": "Primary notebook cleared"}


# ==================== Voice Profile Endpoint ====================

@router.get("/voice-profile")
async def get_voice_profile():
    """Get the user's generated Voice Profile"""
    from services.voice_engine import voice_engine
    profile = voice_engine.get_profile()
    return profile or {}

