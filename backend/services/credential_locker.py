"""Credential Locker Service

Securely stores site credentials for authenticated web scraping.
Credentials are encrypted and stored locally.

Security Notes:
- Credentials are encrypted using Fernet (AES-128)
- Encryption key is derived from machine-specific info
- All storage is local - no cloud transmission
- Users are responsible for ToS compliance on sites they access
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


class SiteCredential(BaseModel):
    """Stored credential for a site."""
    site_domain: str
    site_name: str
    username: str
    # Password is stored encrypted, not in this model
    login_method: str = "email_password"  # email_password, oauth, api_key
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    last_used: Optional[str] = None
    notes: Optional[str] = None


class CredentialLocker:
    """Service for managing encrypted site credentials."""
    
    def __init__(self):
        self._data_dir = Path(os.path.expanduser("~/Library/Application Support/LocalBook"))
        self._credentials_file = self._data_dir / "credentials.enc"
        self._key: Optional[bytes] = None
        self._fernet: Optional[Fernet] = None
    
    def _get_machine_salt(self) -> bytes:
        """Get machine-specific salt for key derivation."""
        # Use combination of machine-specific values
        import platform
        import getpass
        
        machine_info = f"{platform.node()}-{getpass.getuser()}-LocalBook-v1"
        return hashlib.sha256(machine_info.encode()).digest()
    
    def _derive_key(self, master_password: Optional[str] = None) -> bytes:
        """Derive encryption key from machine info and optional master password."""
        salt = self._get_machine_salt()
        
        # If no master password, use a default derived from machine info
        # This provides basic protection but isn't as secure as a user password
        password = (master_password or "LocalBook-Default-Key").encode()
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        
        key = base64.urlsafe_b64encode(kdf.derive(password))
        return key
    
    def _ensure_initialized(self):
        """Ensure the encryption is initialized."""
        if self._fernet is None:
            self._key = self._derive_key()
            self._fernet = Fernet(self._key)
    
    def _load_credentials(self) -> Dict[str, Dict[str, Any]]:
        """Load and decrypt credentials from file."""
        self._ensure_initialized()
        
        if not self._credentials_file.exists():
            return {}
        
        try:
            with open(self._credentials_file, 'rb') as f:
                encrypted = f.read()
            
            decrypted = self._fernet.decrypt(encrypted)
            return json.loads(decrypted.decode())
        except Exception as e:
            print(f"[CREDENTIAL_LOCKER] Failed to load credentials: {e}")
            return {}
    
    def _save_credentials(self, credentials: Dict[str, Dict[str, Any]]):
        """Encrypt and save credentials to file."""
        self._ensure_initialized()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            data = json.dumps(credentials).encode()
            encrypted = self._fernet.encrypt(data)
            
            with open(self._credentials_file, 'wb') as f:
                f.write(encrypted)
        except Exception as e:
            print(f"[CREDENTIAL_LOCKER] Failed to save credentials: {e}")
            raise
    
    async def add_credential(
        self,
        site_domain: str,
        site_name: str,
        username: str,
        password: str,
        login_method: str = "email_password",
        notes: Optional[str] = None
    ) -> SiteCredential:
        """Add or update a credential."""
        credentials = self._load_credentials()
        
        credentials[site_domain] = {
            "site_domain": site_domain,
            "site_name": site_name,
            "username": username,
            "password": password,  # Stored encrypted in file
            "login_method": login_method,
            "created_at": datetime.utcnow().isoformat(),
            "notes": notes,
        }
        
        self._save_credentials(credentials)
        
        return SiteCredential(
            site_domain=site_domain,
            site_name=site_name,
            username=username,
            login_method=login_method,
            notes=notes,
        )
    
    async def get_credential(self, site_domain: str) -> Optional[Dict[str, Any]]:
        """Get credential for a site (includes password)."""
        credentials = self._load_credentials()
        cred = credentials.get(site_domain)
        
        if cred:
            cred["last_used"] = datetime.utcnow().isoformat()
            self._save_credentials(credentials)
        
        return cred
    
    async def list_credentials(self) -> List[SiteCredential]:
        """List all stored credentials (without passwords)."""
        credentials = self._load_credentials()
        
        return [
            SiteCredential(
                site_domain=cred["site_domain"],
                site_name=cred.get("site_name", cred["site_domain"]),
                username=cred["username"],
                login_method=cred.get("login_method", "email_password"),
                created_at=cred.get("created_at", ""),
                last_used=cred.get("last_used"),
                notes=cred.get("notes"),
            )
            for cred in credentials.values()
        ]
    
    async def delete_credential(self, site_domain: str) -> bool:
        """Delete a stored credential."""
        credentials = self._load_credentials()
        
        if site_domain in credentials:
            del credentials[site_domain]
            self._save_credentials(credentials)
            return True
        
        return False
    
    async def test_credential(self, site_domain: str) -> Dict[str, Any]:
        """Test if a credential works (placeholder - would need site-specific logic)."""
        cred = await self.get_credential(site_domain)
        
        if not cred:
            return {"success": False, "error": "Credential not found"}
        
        # TODO: Implement site-specific login testing
        # For now, just return that we have the credential
        return {
            "success": True,
            "message": "Credential exists (login testing not implemented yet)",
            "site_domain": site_domain,
        }
    
    async def has_credential(self, site_domain: str) -> bool:
        """Check if a credential exists for a site."""
        credentials = self._load_credentials()
        return site_domain in credentials


# Singleton instance
credential_locker = CredentialLocker()
