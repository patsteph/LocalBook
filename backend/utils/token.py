"""App token — per-launch random secret used for local auth.

Generated once at backend startup. Persisted to ``data_dir/.app_token`` with
mode 0o600 so only the user account can read it. The Tauri shell reads
this file to inject the token into webview + extension requests.

The token rotates on every backend restart — explicit security choice to
limit blast radius if the file ever leaks. Clients (Tauri webview, Rust
shell, extension) are designed to re-read / re-bootstrap on token rotation
so the user sees no friction across restarts.

P0.1a (2026-05-15).
"""
import os
import secrets
from pathlib import Path
from typing import Optional

_APP_TOKEN: Optional[str] = None
_TOKEN_FILE_NAME = ".app_token"


def generate_app_token() -> str:
    """Return a fresh 64-char hex token (256 bits of entropy)."""
    return secrets.token_hex(32)


def get_app_token() -> Optional[str]:
    """Return the in-memory token. None until ``initialize_app_token`` has been called."""
    return _APP_TOKEN


def initialize_app_token(data_dir: Path) -> str:
    """Generate a token, persist to disk with mode 0o600, set the module global, return it."""
    global _APP_TOKEN
    token = generate_app_token()
    token_path = data_dir / _TOKEN_FILE_NAME
    token_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic create with restrictive mode — file is 0o600 before any bytes land in it.
    fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    _APP_TOKEN = token
    return token
