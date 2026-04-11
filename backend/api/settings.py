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
    in parallel, classifies each model into main / fast / embeddings, and
    appends current-active state from settings.  Cached in-process for 30 s.
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
        except Exception:
            pass
        return {}

    def _classify_model(name: str, show: dict, size_bytes: int) -> str:
        """
        Auto-classify an installed model into main / fast / embeddings.

        Rules (checked in order):
        1. Name contains an embedding keyword → embeddings
        2. Model reports embedding_only capability → embeddings
        3. Parameter count >= 7B → main (regardless of quantization size)
        4. Size on disk >= 5 GB → main
        5. Otherwise → fast
        """
        lower = name.lower()
        embed_keywords = ("embed", "nomic", "mxbai", "bge", "minilm", "gte-")
        if any(k in lower for k in embed_keywords):
            return "embeddings"

        model_info_caps = show.get("model_info", {})
        if model_info_caps.get("general.architecture") == "bert":
            return "embeddings"

        # Extract parameter count from name (e.g., "7B", "8B", "70b")
        import re as _re
        param_match = _re.search(r'(\d+)([bB])', name)
        if param_match:
            param_count = int(param_match.group(1))
            if param_count >= 7:
                return "main"

        # Also check Ollama metadata for parameter count
        meta_params = model_info_caps.get("general.parameter_count")
        if meta_params and isinstance(meta_params, (int, float)):
            if meta_params >= 7_000_000_000:  # 7B parameters
                return "main"

        size_gb = size_bytes / (1024 ** 3)
        if size_gb >= 4.0:
            return "main"
        return "fast"

    def _estimate_ram(size_bytes: int) -> float:
        """Estimate required RAM in GB: disk size * 1.25 (weights + KV cache)."""
        return round(size_bytes / (1024 ** 3) * 1.25, 1)

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
                    except ValueError:
                        pass
        return 4096

    from evaluator.model_registry import model_registry  # hoisted — one import for all enrichments

    async with httpx.AsyncClient() as client:
        raw_models = await _fetch_tags(client)

        semaphore = asyncio.Semaphore(4)

        async def _enrich(m: dict) -> dict:
            async with semaphore:
                name = m.get("name", "")
                size_bytes = m.get("size", 0)
                show = await _fetch_show(client, name)

                suggested_role = _classify_model(name, show, size_bytes)
                ram_required = _estimate_ram(size_bytes)
                context_window = _parse_context(show)

                reg = model_registry.get_model(name)

                # Detect vision for community models via Ollama show data
                if reg:
                    _vision = reg.supports_vision
                else:
                    _mi = show.get("model_info", {})
                    _vision = (
                        "vision" in name.lower()
                        or "llava" in name.lower()
                        or any("projector" in k for k in _mi)
                        or _mi.get("clip.has_vision_encoder", False)
                    )

                return {
                    "name": name,
                    "display_name": (reg.display_name if reg else name.split(":")[0].replace("-", " ").title()),
                    "size_bytes": size_bytes,
                    "size_gb": round(size_bytes / (1024 ** 3), 1),
                    "ram_required_gb": ram_required,
                    "context_window": context_window,
                    "suggested_role": suggested_role,
                    "supports_vision": _vision,
                    "supports_json_mode": (reg.supports_json_mode if reg else False),
                    "vendor": (reg.vendor if reg else ""),
                    "origin_country": (reg.origin_country if reg else ""),
                    "parameter_count": (reg.parameter_count if reg else ""),
                    "modified_at": m.get("modified_at", ""),
                    "in_registry": reg is not None,
                }

        enriched = await asyncio.gather(*[_enrich(m) for m in raw_models])

    # Attach active-role flags from current settings
    active = {
        "main": app_settings.ollama_model,
        "fast": app_settings.ollama_fast_model,
        "embeddings": app_settings.embedding_model,
        "vision": app_settings.vision_model,
    }
    for m in enriched:
        m["active_as"] = next(
            (role for role, active_name in active.items() if active_name == m["name"]),
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
