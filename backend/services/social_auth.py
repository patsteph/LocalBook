"""Social Auth Service — Fernet-encrypted Playwright session management.

Handles one-time platform authentication via a real browser window,
encrypts the session state before writing to disk, and decrypts
in memory for headless collection.

Security Architecture:
- Session state captured as dict in memory via context.storage_state()
- Encrypted with Fernet AES-128 (reuses credential_locker infrastructure)
- Written to disk only as .enc binary — no plain-text JSON on disk
- Decrypted in memory only when needed, then zeroed
- Files set to chmod 600, excluded from Time Machine
- 30-day max session age with forced re-auth
"""

import os
import sys
import json
import stat
import subprocess
import logging
import ctypes
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from config import settings
from models.person_profile import (
    SocialAuthConfig, SocialPlatform,
    PLATFORM_LOGIN_URLS, PLATFORM_AUTH_SUCCESS_PATTERNS,
)


def _ensure_playwright_browsers_path():
    """Point Playwright at the system browser cache and auto-install if missing.

    When running inside a PyInstaller onedir bundle, Playwright's default
    browser path resolves to _internal/playwright/driver/... which doesn't
    contain the actual Chromium binaries. We ALWAYS set PLAYWRIGHT_BROWSERS_PATH
    to the system cache location and auto-install chromium if needed.

    Install strategies (tried in order):
    1. Bundled driver: use playwright's own node+cli.js (works in PyInstaller)
    2. Python module: sys.executable -m playwright install (works in source builds)
    3. System CLI: playwright install (works if globally installed)
    """
    _log = logging.getLogger(__name__)

    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        # Already set — but verify browsers actually exist there
        browsers_path = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
        if browsers_path.is_dir() and any(browsers_path.glob("chromium-*")):
            return  # browsers exist, we're good
        # Path is set but empty/missing — fall through to install

    import platform as plat

    # Determine system cache location
    if plat.system() == "Darwin":
        system_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        system_cache = Path.home() / ".cache" / "ms-playwright"

    # ALWAYS set the env var — even if the dir doesn't exist yet.
    # This prevents Playwright from looking inside the PyInstaller bundle.
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(system_cache)

    # Check if chromium is already installed
    if system_cache.is_dir() and any(system_cache.glob("chromium-*")):
        return  # browsers already installed

    _log.info("[Playwright] Chromium not found at %s — auto-installing...", system_cache)
    install_env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(system_cache)}

    # Strategy 1: Use the bundled Playwright driver directly.
    # This works in PyInstaller bundles where sys.executable is the frozen binary
    # and `python -m playwright` doesn't work. The driver (node + cli.js) IS
    # bundled by --collect-all=playwright and compute_driver_executable() finds it.
    try:
        from playwright._impl._driver import compute_driver_executable
        driver_executable, driver_cli = compute_driver_executable()
        _log.info("[Playwright] Using bundled driver: %s %s", driver_executable, driver_cli)
        # Ensure bundled node binary is executable (may lose +x after zip extraction)
        driver_path = Path(driver_executable)
        if driver_path.exists() and not os.access(driver_path, os.X_OK):
            os.chmod(driver_path, driver_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        result = subprocess.run(
            [str(driver_executable), str(driver_cli), "install", "chromium"],
            capture_output=True, text=True, timeout=180,
            env=install_env,
        )
        if result.returncode == 0:
            _log.info("[Playwright] Chromium installed successfully via bundled driver")
            return
        else:
            _log.warning(
                "[Playwright] Bundled driver install returned code %d: %s",
                result.returncode, (result.stderr or result.stdout)[:500]
            )
    except Exception as e:
        _log.debug("[Playwright] Bundled driver strategy failed: %s", e)

    # Strategy 2: Python module (works for source builds)
    if not getattr(sys, 'frozen', False):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True, text=True, timeout=180,
                env=install_env,
            )
            if result.returncode == 0:
                _log.info("[Playwright] Chromium installed via python -m playwright")
                return
            else:
                _log.warning(
                    "[Playwright] python -m playwright install returned code %d: %s",
                    result.returncode, (result.stderr or result.stdout)[:500]
                )
        except Exception as e:
            _log.debug("[Playwright] Python module strategy failed: %s", e)

    # Strategy 3: System CLI fallback
    try:
        result = subprocess.run(
            ["playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=180,
            env=install_env,
        )
        if result.returncode == 0:
            _log.info("[Playwright] Chromium installed via system playwright CLI")
            return
    except Exception:
        pass

    _log.error(
        "[Playwright] All auto-install strategies failed. "
        "Please run manually: playwright install chromium"
    )

logger = logging.getLogger(__name__)

# Auth state directory
AUTH_DIR = settings.data_dir / "auth"


class SocialAuthService:
    """Manages encrypted social platform authentication sessions."""

    def __init__(self):
        self._ensure_auth_dir()

    def _ensure_auth_dir(self):
        """Create auth directory with restrictive permissions."""
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(AUTH_DIR, stat.S_IRWXU)  # 700 — owner only
            # Exclude from Time Machine backups
            subprocess.run(
                ["tmutil", "addexclusion", str(AUTH_DIR)],
                check=False, capture_output=True
            )
        except Exception as e:
            logger.warning(f"Could not set auth dir permissions: {e}")

    def _get_enc_path(self, platform: str) -> Path:
        return AUTH_DIR / f"{platform}_state.enc"

    def _get_fernet(self):
        """Get the Fernet cipher from credential_locker (lazy import)."""
        from services.credential_locker import credential_locker
        credential_locker._ensure_initialized()
        return credential_locker._fernet

    def _encrypt_state(self, state: dict) -> bytes:
        """Encrypt a session state dict to bytes."""
        fernet = self._get_fernet()
        state_bytes = json.dumps(state).encode("utf-8")
        return fernet.encrypt(state_bytes)

    def _decrypt_state(self, encrypted: bytes) -> dict:
        """Decrypt bytes to a session state dict."""
        fernet = self._get_fernet()
        state_bytes = fernet.decrypt(encrypted)
        state = json.loads(state_bytes)
        # Zero out the decrypted bytes in memory
        try:
            ctypes.memset(ctypes.c_char_p(state_bytes), 0, len(state_bytes))
        except Exception:
            pass
        return state

    def _save_encrypted_state(self, platform: str, state: dict) -> str:
        """Encrypt and save session state to .enc file. Returns path."""
        encrypted = self._encrypt_state(state)
        enc_path = self._get_enc_path(platform)

        with open(enc_path, "wb") as f:
            f.write(encrypted)

        # Restrictive permissions
        os.chmod(enc_path, stat.S_IRUSR | stat.S_IWUSR)  # 600
        logger.info(f"Session state saved (encrypted): {enc_path}")
        return str(enc_path)

    def load_session_state(self, platform: str) -> Optional[dict]:
        """Load and decrypt session state for a platform. Returns None if not found."""
        enc_path = self._get_enc_path(platform)
        if not enc_path.exists():
            return None

        try:
            with open(enc_path, "rb") as f:
                encrypted = f.read()
            return self._decrypt_state(encrypted)
        except Exception as e:
            logger.error(f"Failed to decrypt session for {platform}: {e}")
            return None

    async def authenticate_platform(self, platform: str) -> SocialAuthConfig:
        """Open a real browser for the user to log in. Encrypt and save session.
        
        This launches a VISIBLE browser window. The user logs in manually
        (including 2FA). Once authenticated, the session is captured in memory,
        encrypted, and saved to disk. No plain-text tokens touch the filesystem.
        """
        _ensure_playwright_browsers_path()
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )

        platform_enum = SocialPlatform(platform)
        login_url = PLATFORM_LOGIN_URLS.get(platform_enum)
        success_pattern = PLATFORM_AUTH_SUCCESS_PATTERNS.get(platform_enum)

        if not login_url:
            raise ValueError(f"No login URL configured for platform: {platform}")

        logger.info(f"Starting authentication for {platform} — opening browser...")

        async with async_playwright() as p:
            # Launch VISIBLE browser — user logs in manually
            try:
                browser = await p.chromium.launch(
                    headless=False,
                    channel="chromium",
                )
            except Exception as launch_err:
                err_str = str(launch_err)
                if "Executable doesn't exist" in err_str or "browserType.launch" in err_str.lower():
                    raise RuntimeError(
                        "Chromium browser not found. LocalBook tried to auto-install it but failed. "
                        "Please open a terminal and run:\n\n"
                        "  pip install playwright && playwright install chromium\n\n"
                        "Then try connecting again."
                    ) from launch_err
                raise
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            try:
                await page.goto(login_url)

                # Wait for user to complete login (up to 2 minutes for 2FA)
                if success_pattern:
                    logger.info(f"Waiting for auth success (pattern: {success_pattern})...")
                    await page.wait_for_url(success_pattern, timeout=120_000)
                else:
                    # Fallback: wait for navigation away from login page
                    await page.wait_for_timeout(5000)
                    await page.wait_for_load_state("networkidle")

                logger.info(f"Authentication successful for {platform}")

                # Capture session state as dict (in memory only)
                state = await context.storage_state()

                # Encrypt and save
                enc_path = self._save_encrypted_state(platform, state)

                config = SocialAuthConfig(
                    platform=platform,
                    authenticated=True,
                    auth_state_path=enc_path,
                    last_verified=datetime.utcnow().isoformat(),
                    created_at=datetime.utcnow().isoformat(),
                )

                return config

            except Exception as e:
                logger.error(f"Authentication failed for {platform}: {e}")
                raise
            finally:
                await context.close()
                await browser.close()

    def verify_session(self, platform: str) -> Dict[str, Any]:
        """Check if a saved session exists and hasn't expired."""
        enc_path = self._get_enc_path(platform)

        if not enc_path.exists():
            return {
                "platform": platform,
                "authenticated": False,
                "reason": "no_session",
            }

        # Check file age
        file_age_days = (
            datetime.utcnow() - datetime.fromtimestamp(enc_path.stat().st_mtime)
        ).days

        if file_age_days > 30:
            return {
                "platform": platform,
                "authenticated": False,
                "reason": "expired",
                "age_days": file_age_days,
            }

        # Try to decrypt (validates the file isn't corrupted)
        state = self.load_session_state(platform)
        if state is None:
            return {
                "platform": platform,
                "authenticated": False,
                "reason": "decrypt_failed",
            }

        return {
            "platform": platform,
            "authenticated": True,
            "age_days": file_age_days,
            "cookies_count": len(state.get("cookies", [])),
        }

    def get_auth_status(self) -> Dict[str, Dict[str, Any]]:
        """Get authentication status for all supported platforms."""
        status = {}
        for platform in SocialPlatform:
            if platform == SocialPlatform.PERSONAL_SITE:
                continue  # No auth needed for personal sites
            if platform == SocialPlatform.GITHUB:
                # GitHub public data doesn't need auth
                status[platform.value] = {
                    "platform": platform.value,
                    "authenticated": True,
                    "reason": "no_auth_needed",
                }
                continue
            status[platform.value] = self.verify_session(platform.value)
        return status

    def disconnect_platform(self, platform: str) -> bool:
        """Remove saved session for a platform."""
        enc_path = self._get_enc_path(platform)
        if enc_path.exists():
            # Overwrite with zeros before deleting
            try:
                file_size = enc_path.stat().st_size
                with open(enc_path, "wb") as f:
                    f.write(b"\x00" * file_size)
            except Exception:
                pass
            enc_path.unlink(missing_ok=True)
            logger.info(f"Disconnected platform: {platform}")
            return True
        return False


# Singleton
social_auth = SocialAuthService()
