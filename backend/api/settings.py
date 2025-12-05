"""Settings API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import keyring
from config import settings

router = APIRouter()

# Service name for keychain storage
SERVICE_NAME = "LocalBook"

class SetAPIKeyRequest(BaseModel):
    key_name: str
    value: str

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
        "provider": settings.llm_provider
    }

def get_api_key(key_name: str) -> str | None:
    """Helper function to get an API key from the keychain"""
    try:
        return keyring.get_password(SERVICE_NAME, key_name)
    except Exception:
        return None
