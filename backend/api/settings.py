"""Settings API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import keyring
import json
from config import settings

router = APIRouter()

# Service name for keychain storage
SERVICE_NAME = "LocalBook"

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
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "custom_llm",
    ]

    configured = {}
    for key_name in key_names:
        try:
            value = keyring.get_password(SERVICE_NAME, key_name)
            configured[key_name] = value is not None and len(value) > 0
        except Exception:
            configured[key_name] = False

    return APIKeysStatusResponse(configured=configured)

@router.post("/api-keys/set")
async def set_api_key(request: SetAPIKeyRequest):
    """Set an API key in the system keychain"""
    try:
        keyring.set_password(SERVICE_NAME, request.key_name, request.value)
        return {"message": f"API key '{request.key_name}' saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save API key: {str(e)}")

@router.delete("/api-keys/{key_name}")
async def delete_api_key(key_name: str):
    """Delete an API key from the system keychain"""
    try:
        keyring.delete_password(SERVICE_NAME, key_name)
        return {"message": f"API key '{key_name}' deleted successfully"}
    except keyring.errors.PasswordDeleteError:
        # Key doesn't exist, that's OK
        return {"message": f"API key '{key_name}' was not configured"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {str(e)}")

@router.get("/llm-info")
async def get_llm_info():
    """Get current LLM model information"""
    return {
        "model_name": settings.ollama_model,
        "fast_model_name": settings.ollama_fast_model,
        "provider": settings.llm_provider
    }

def get_api_key(key_name: str) -> str | None:
    """Helper function to get an API key from the keychain"""
    try:
        return keyring.get_password(SERVICE_NAME, key_name)
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
