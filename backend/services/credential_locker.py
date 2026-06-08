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


# ---------------------------------------------------------------------------
# Phase 6 — IMAP credential extension for the Correspondent agent.
# Stored alongside web credentials in the same encrypted file but keyed under
# `imap:<email>` to avoid collision with site_domain entries. Adds the IMAP
# host / port / SSL flag and persists per-account poller state (last_uid +
# last_polled_at) so the polling loop is resumable across restarts.
# ---------------------------------------------------------------------------


class IMapCredential(BaseModel):
    """IMAP account credential record (no plaintext password in this view)."""

    email: str
    imap_host: str
    imap_port: int = 993
    imap_user: str
    use_ssl: bool = True
    enabled: bool = True
    last_uid: int = 0
    last_polled_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    # Phase 8 — outbound SMTP for reply-to-ingest confirmations + weekly journal.
    smtp_host: Optional[str] = None
    smtp_port: int = 465
    smtp_use_tls: bool = True
    send_confirmations: bool = True
    # Phase 8 — fallback target when a forward has no #slug and the router
    # can't decide. Null → forwards go to the routing queue for user pick.
    default_forward_notebook_id: Optional[str] = None
    # Phase 13 — weekly auto-journal (K). Defaults ON; scheduler honors
    # the flag + last_journal_at gate.
    weekly_journal_enabled: bool = True
    last_journal_at: Optional[str] = None


def _imap_key(email: str) -> str:
    """Namespace IMAP entries so they don't collide with web logins."""
    return f"imap:{email.lower().strip()}"


async def add_imap_account(
    *,
    email: str,
    imap_host: str,
    imap_port: int,
    imap_user: str,
    imap_password: str,
    use_ssl: bool = True,
    smtp_host: Optional[str] = None,
    smtp_port: int = 465,
    smtp_use_tls: bool = True,
    send_confirmations: bool = True,
    default_forward_notebook_id: Optional[str] = None,
) -> IMapCredential:
    """Store an IMAP credential. Overwrites any prior entry for the same email."""
    # Preserve existing per-account state (last_uid, last_polled_at) on overwrite.
    prior = await credential_locker.get_credential(_imap_key(email))
    prior_state = _parse_imap_notes((prior or {}).get("notes")) if prior else {}
    state = {
        "email": email,
        "imap_host": imap_host,
        "imap_port": imap_port,
        "use_ssl": use_ssl,
        "enabled": True,
        "last_uid": prior_state.get("last_uid", 0),
        "last_polled_at": prior_state.get("last_polled_at"),
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_use_tls": smtp_use_tls,
        "send_confirmations": send_confirmations,
        "default_forward_notebook_id": default_forward_notebook_id,
        "weekly_journal_enabled": prior_state.get("weekly_journal_enabled", True),
        "last_journal_at": prior_state.get("last_journal_at"),
    }
    await credential_locker.add_credential(
        site_domain=_imap_key(email),
        site_name=f"IMAP: {email}",
        username=imap_user,
        password=imap_password,
        login_method="imap_app_password",
        notes=json.dumps(state),
    )
    return IMapCredential(
        email=email,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_user=imap_user,
        use_ssl=use_ssl,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_use_tls=smtp_use_tls,
        send_confirmations=send_confirmations,
        default_forward_notebook_id=default_forward_notebook_id,
    )


def _parse_imap_notes(notes: Optional[str]) -> Dict[str, Any]:
    if not notes:
        return {}
    try:
        return json.loads(notes)
    except Exception:
        return {}


async def get_imap_account(email: str) -> Optional[Dict[str, Any]]:
    """Return the full IMAP credential including password and poller state."""
    cred = await credential_locker.get_credential(_imap_key(email))
    if not cred:
        return None
    state = _parse_imap_notes(cred.get("notes"))
    return {
        "email": state.get("email", email),
        "imap_host": state.get("imap_host", ""),
        "imap_port": state.get("imap_port", 993),
        "imap_user": cred.get("username", ""),
        "imap_password": cred.get("password", ""),
        "use_ssl": state.get("use_ssl", True),
        "enabled": state.get("enabled", True),
        "last_uid": state.get("last_uid", 0),
        "last_polled_at": state.get("last_polled_at"),
        # Phase 8
        "smtp_host": state.get("smtp_host"),
        "smtp_port": state.get("smtp_port", 465),
        "smtp_use_tls": state.get("smtp_use_tls", True),
        "send_confirmations": state.get("send_confirmations", True),
        "default_forward_notebook_id": state.get("default_forward_notebook_id"),
        "weekly_journal_enabled": state.get("weekly_journal_enabled", True),
        "last_journal_at": state.get("last_journal_at"),
    }


async def list_imap_accounts() -> List[IMapCredential]:
    """List stored IMAP accounts (no passwords)."""
    all_creds = await credential_locker.list_credentials()
    out: List[IMapCredential] = []
    for c in all_creds:
        if c.login_method != "imap_app_password":
            continue
        state = _parse_imap_notes(c.notes)
        out.append(IMapCredential(
            email=state.get("email", c.site_domain.replace("imap:", "", 1)),
            imap_host=state.get("imap_host", ""),
            imap_port=state.get("imap_port", 993),
            imap_user=c.username,
            use_ssl=state.get("use_ssl", True),
            enabled=state.get("enabled", True),
            last_uid=state.get("last_uid", 0),
            last_polled_at=state.get("last_polled_at"),
            created_at=c.created_at,
            smtp_host=state.get("smtp_host"),
            smtp_port=state.get("smtp_port", 465),
            smtp_use_tls=state.get("smtp_use_tls", True),
            send_confirmations=state.get("send_confirmations", True),
            default_forward_notebook_id=state.get("default_forward_notebook_id"),
        ))
    return out


async def delete_imap_account(email: str) -> bool:
    return await credential_locker.delete_credential(_imap_key(email))


async def update_imap_state(
    email: str,
    *,
    last_uid: Optional[int] = None,
    last_polled_at: Optional[str] = None,
    enabled: Optional[bool] = None,
    send_confirmations: Optional[bool] = None,
    default_forward_notebook_id: Optional[str] = None,
    weekly_journal_enabled: Optional[bool] = None,
    last_journal_at: Optional[str] = None,
) -> bool:
    """Update per-account poller state. Returns False if the account is missing."""
    creds = credential_locker._load_credentials()  # internal; same module
    key = _imap_key(email)
    if key not in creds:
        return False
    state = _parse_imap_notes(creds[key].get("notes"))
    if last_uid is not None:
        state["last_uid"] = last_uid
    if last_polled_at is not None:
        state["last_polled_at"] = last_polled_at
    if enabled is not None:
        state["enabled"] = enabled
    if send_confirmations is not None:
        state["send_confirmations"] = send_confirmations
    if default_forward_notebook_id is not None:
        state["default_forward_notebook_id"] = default_forward_notebook_id or None
    if weekly_journal_enabled is not None:
        state["weekly_journal_enabled"] = weekly_journal_enabled
    if last_journal_at is not None:
        state["last_journal_at"] = last_journal_at
    creds[key]["notes"] = json.dumps(state)
    credential_locker._save_credentials(creds)
    return True


# Singleton instance
credential_locker = CredentialLocker()
