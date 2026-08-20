"""Settings API endpoints"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import json
import threading
from config import settings

logger = logging.getLogger(__name__)
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

# ── Model browser (catalog) ─────────────────────────────────────────────────────
# Discovery, as opposed to the Locker's "what is already on disk". Lives here rather than in
# its own router so the download endpoints below stay next to the browse endpoints that
# trigger them.

@router.get("/catalog")
async def catalog_search(
    q: str = "",
    sort: str = "downloads",
    limit: int = 40,
    role: str = "",
    fits_only: bool = False,
    include_blocked: bool = False,
):
    """Browse MLX models on Hugging Face.

    Needs the network — the one place in the app that legitimately does. Returns
    `offline: true` with a reason rather than erroring, so the panel can say so plainly.
    """
    from services.model_catalog import search
    import asyncio
    # HF is a blocking httpx call; keep it off the event loop.
    return await asyncio.to_thread(
        search, query=q, sort=sort, limit=max(1, min(limit, 100)),
        role=role, include_blocked=include_blocked, fits_only=fits_only,
    )


@router.get("/catalog/card")
async def catalog_card(model_id: str):
    """Full detail + README for one model — the model-card popup."""
    import asyncio
    from services.model_catalog import card
    return await asyncio.to_thread(card, model_id)


@router.post("/catalog/download")
async def catalog_download(payload: dict):
    """Start downloading a catalog model. Returns immediately; poll /settings/mlx/downloads.

    Refuses a blocked-origin model here as well as in search: the browse filter is a UI
    convenience, and this endpoint is what actually puts weights on the disk.
    """
    model_id = (payload or {}).get("model_id") or ""
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")
    from services.model_catalog import origin_of
    org = origin_of(model_id, (payload or {}).get("tags") or [])
    if not org["allowed"]:
        raise HTTPException(
            status_code=403,
            detail=f"{org['vendor']} ({org['country']}) is excluded by policy — not downloaded.",
        )
    from services.mlx_download import mlx_download_manager
    return await mlx_download_manager.start(model_id)


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

    # Every card comes from the MLX block below. The Ollama half — a /api/tags list, a
    # /api/show per model, then classification by disk size — is gone with the models it
    # described; nothing could load them.
    enriched: list = []

    # MLX models — the only ones listed. Caps via a config.json probe (no model load);
    # RAM-fit via model_sizing; presence via a real weight check. Never fatal.
    try:
        from services.mlx_engine import MLXEngine as _MLXEngine
        _mlx_ok = _MLXEngine.available()
    except Exception:
        _mlx_ok = False
    if _mlx_ok:
        try:
            from evaluator.capability_probe import probe_capabilities as _mprobe
            from services.model_presence import is_present as _is_present
            # `services.hardware_profiler` NEVER EXISTED — this import raised on every
            # call, so the whole fit block below was dead and no MLX card was ever
            # size-checked. `services.model_sizing` reads exact weight bytes + real KV
            # geometry and derives the budget from the GPU's addressable working set.
            from services import model_sizing as _sizing
            _seen = {e.get("name") for e in enriched}
            _mlx_ids = dict.fromkeys(
                getattr(app_settings, k, None)
                for k in ("main_model", "fast_model", "vision_model",
                          "embedding_model"))
            for _mid in [x for x in _mlx_ids if x and x not in _seen]:
                _c = _mprobe(_mid, provider="mlx")
                if not _c:
                    continue
                # Constrain each MLX card to the role SLOT it fills in config, not every
                # role its capabilities allow — so the MLX gemma shows only under Main
                # (+ Vision) and MLX phi only under Fast, mirroring their Ollama
                # counterparts instead of flooding both columns (user #2).
                _role_slots = []
                if _mid == getattr(app_settings, "main_model", None):
                    _role_slots.append("main_model")
                if _mid == getattr(app_settings, "fast_model", None):
                    _role_slots.append("fast_model")
                if _mid == getattr(app_settings, "vision_model", None):
                    _role_slots.append("vision_model")
                if _mid == getattr(app_settings, "embedding_model", None):
                    _role_slots.append("embedding_model")
                _roles = list(dict.fromkeys(_role_slots)) or _c.roles()
                _sr = ("fast" if _role_slots == ["fast_model"]
                       else "vision" if _role_slots == ["vision_model"]
                       else "embeddings" if _role_slots == ["embedding_model"]
                       else "main")
                # `try_to_load_from_cache(_mid, "config.json")` was the old test — it is
                # true for a download that fetched the config and then died, which is
                # exactly the state that must NOT read as installed. `is_present` requires
                # real weight bytes.
                _installed = _is_present(_mid)
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
                try:
                    # Size against the DEPLOYED window, not the native one: gemma's native
                    # 131k costs 1.78 GiB of KV where its deployed 16k costs 0.25 GiB, and
                    # judging a card by a context it will never run at is how a usable model
                    # gets marked "over".
                    _ctx = min(int(_c.native_ctx or 8192), 16384)
                    _f = _sizing.fit(_mid, _ctx)
                    if _f.get("fits") is not None:
                        _card["ram_fit"] = {"fits": _f["fits"],
                                            "recommendation": _f["recommendation"]}
                    if _f.get("total_needed_gb"):
                        _card["ram_required_gb"] = round(float(_f["total_needed_gb"]), 1)
                except Exception as _fit_e:
                    logger.debug(f"[settings] fit calc failed for {_mid}: {_fit_e}")
                # LLM Studio lists ONLY models verified present in the local cache. A
                # card for something not downloaded is an offer the app cannot honour;
                # acquiring new models belongs to the download manager, not this list.
                if _installed:
                    enriched.append(_card)
        except Exception as _mlx_e:
            logger.debug(f"[settings] MLX model enumeration failed: {_mlx_e}")

    # Active role → model. Each attribute IS the live checkpoint since the role collapse.
    active = {
        "main": app_settings.main_model,
        "fast": app_settings.fast_model,
        "embeddings": app_settings.embedding_model,
        "vision": app_settings.vision_model,
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
        "model_name": settings.main_model,
        "fast_model_name": settings.fast_model,
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

