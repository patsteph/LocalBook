"""Credential Locker API endpoints

Manage encrypted site credentials for authenticated web scraping.

IMPORTANT: This feature is for personal research use only.
Users are responsible for complying with site Terms of Service.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.credential_locker import credential_locker


router = APIRouter(prefix="/credentials", tags=["credentials"])


# =============================================================================
# Request/Response Models
# =============================================================================

class AddCredentialRequest(BaseModel):
    site_domain: str = Field(description="Domain of the site (e.g., 'medium.com')")
    site_name: str = Field(description="Display name for the site")
    username: str = Field(description="Username or email")
    password: str = Field(description="Password (will be encrypted)")
    login_method: str = Field(default="email_password", description="Login method")
    notes: Optional[str] = Field(default=None, description="Optional notes")


class CredentialResponse(BaseModel):
    site_domain: str
    site_name: str
    username: str
    login_method: str
    created_at: str
    last_used: Optional[str] = None
    notes: Optional[str] = None


class TestCredentialResponse(BaseModel):
    success: bool
    message: str
    site_domain: str


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/", response_model=List[CredentialResponse])
async def list_credentials():
    """
    List all stored credentials (passwords are NOT returned).
    
    Returns metadata about stored credentials without exposing passwords.
    """
    credentials = await credential_locker.list_credentials()
    return [
        CredentialResponse(
            site_domain=c.site_domain,
            site_name=c.site_name,
            username=c.username,
            login_method=c.login_method,
            created_at=c.created_at,
            last_used=c.last_used,
            notes=c.notes,
        )
        for c in credentials
    ]


@router.post("/", response_model=CredentialResponse)
async def add_credential(request: AddCredentialRequest):
    """
    Add or update a site credential.
    
    The password will be encrypted before storage.
    If a credential for this site already exists, it will be updated.
    
    ⚠️ DISCLAIMER: This feature stores credentials locally for personal use.
    You are responsible for complying with each site's Terms of Service.
    """
    credential = await credential_locker.add_credential(
        site_domain=request.site_domain,
        site_name=request.site_name,
        username=request.username,
        password=request.password,
        login_method=request.login_method,
        notes=request.notes,
    )
    
    return CredentialResponse(
        site_domain=credential.site_domain,
        site_name=credential.site_name,
        username=credential.username,
        login_method=credential.login_method,
        created_at=credential.created_at,
        last_used=credential.last_used,
        notes=credential.notes,
    )


@router.delete("/{site_domain}")
async def delete_credential(site_domain: str):
    """Delete a stored credential."""
    success = await credential_locker.delete_credential(site_domain)
    
    if not success:
        raise HTTPException(status_code=404, detail="Credential not found")
    
    return {"message": "Credential deleted", "site_domain": site_domain}


@router.get("/{site_domain}/exists")
async def check_credential_exists(site_domain: str):
    """Check if a credential exists for a site (doesn't expose password)."""
    exists = await credential_locker.has_credential(site_domain)
    return {"site_domain": site_domain, "exists": exists}


@router.post("/{site_domain}/test", response_model=TestCredentialResponse)
async def test_credential(site_domain: str):
    """
    Test if a stored credential works.
    
    Note: Full login testing is not yet implemented for all sites.
    This currently just verifies the credential exists.
    """
    result = await credential_locker.test_credential(site_domain)
    
    return TestCredentialResponse(
        success=result.get("success", False),
        message=result.get("message", result.get("error", "Unknown error")),
        site_domain=site_domain,
    )


@router.get("/disclaimer")
async def get_disclaimer():
    """Get the legal disclaimer for credential storage."""
    return {
        "title": "Credential Storage Disclaimer",
        "message": """
This feature stores site credentials locally on your device for personal research use.

IMPORTANT:
• Credentials are encrypted and stored only on YOUR device
• No credentials are transmitted to any cloud service
• You are responsible for complying with each site's Terms of Service
• Use this feature responsibly and legally
• LocalBook is not responsible for how you use stored credentials

By storing credentials, you acknowledge that you have read and accepted
the Terms of Service of the sites for which you're storing credentials.
        """.strip(),
        "accepted_storage_key": "credential_disclaimer_accepted"
    }
