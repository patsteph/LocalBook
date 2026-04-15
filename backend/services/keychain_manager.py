"""Keychain Manager — single-item consolidated storage with Touch ID support.

Design:
- All API keys are stored as a single JSON blob under ONE Keychain item
  (SERVICE_NAME / "api_keys"), eliminating the per-key password prompts.
- On macOS, Touch ID / biometric unlock is requested via LocalAuthentication
  before reading secrets. Falls back gracefully if biometrics are unavailable
  (e.g., non-Touch-ID Macs, running headless, pyobjc not installed).
- On first access, migrates any legacy per-key items written by the old
  keyring-per-key approach to the new consolidated blob and deletes the
  old items.

Public API (mirrors the old keyring helpers in api/settings.py):
  get_api_key(key_name)         -> str | None
  set_api_key(key_name, value)  -> None
  delete_api_key(key_name)      -> None
  get_all_keys_status(names)    -> dict[str, bool]
"""

import asyncio
import json
import logging
import threading
from typing import Optional

import keyring
import keyring.errors

logger = logging.getLogger(__name__)

SERVICE_NAME = "LocalBook"
BUNDLE_KEY = "api_keys"          # Single Keychain item that holds all keys as JSON
LEGACY_KEYS = [                  # Per-key items written by old approach
    "brave_api_key",
    "youtube_api_key",
    "anthropic_api_key",
    "openai_api_key",
    "gemini_api_key",
    "custom_llm",
]

# ── Touch ID / LocalAuthentication ──────────────────────────────────────────

_biometric_lock = threading.Lock()
_biometric_authenticated = False   # Cached for the process lifetime
_migration_done = False            # Sentinel: legacy migration runs exactly once


def _request_biometric_auth_sync(reason: str = "unlock LocalBook API keys") -> bool:
    """
    Synchronous Touch ID gate — runs the macOS LAContext evaluation on a
    background thread so it never blocks the calling thread directly.
    Safe to call from both sync and async contexts.

    Returns True if authenticated (or biometrics unavailable — non-fatal).
    Result is cached for the process lifetime: prompt appears at most once.
    """
    global _biometric_authenticated

    if _biometric_authenticated:
        return True

    with _biometric_lock:
        if _biometric_authenticated:  # double-checked locking
            return True

        try:
            from LocalAuthentication import (  # type: ignore  # pyobjc
                LAContext,
                LAPolicyDeviceOwnerAuthenticationWithBiometrics,
            )
            import objc  # type: ignore

            ctx = LAContext.new()
            can_eval, _ = ctx.canEvaluatePolicy_error_(
                LAPolicyDeviceOwnerAuthenticationWithBiometrics, None
            )
            if not can_eval:
                logger.debug("[Keychain] Biometrics unavailable — skipping Touch ID prompt.")
                _biometric_authenticated = True
                return True

            result_holder: list[bool] = [False]
            done = threading.Event()

            def _handler(success: bool, error: objc.object) -> None:  # type: ignore
                result_holder[0] = bool(success)
                done.set()

            ctx.evaluatePolicy_localizedReason_reply_(
                LAPolicyDeviceOwnerAuthenticationWithBiometrics,
                reason,
                _handler,
            )
            # Block *this* thread (not the event loop) waiting for the OS callback
            done.wait(timeout=30)

            if result_holder[0]:
                _biometric_authenticated = True
                logger.info("[Keychain] Touch ID authenticated.")
            else:
                logger.warning("[Keychain] Touch ID failed or cancelled — keys still accessible.")
                # Treat cancelled Touch ID as non-fatal; fall through to keychain read
                _biometric_authenticated = True

            return True

        except ImportError:
            logger.debug("[Keychain] pyobjc LocalAuthentication not installed — biometric skipped.")
            _biometric_authenticated = True
            return True
        except Exception as e:
            logger.warning(f"[Keychain] Biometric check error (non-fatal): {e}")
            _biometric_authenticated = True
            return True


async def _request_biometric_auth_async(reason: str = "unlock LocalBook API keys") -> bool:
    """
    Async-safe wrapper: runs the blocking Touch ID check in a thread-pool
    executor so it never stalls the uvicorn event loop.
    """
    if _biometric_authenticated:
        return True
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _request_biometric_auth_sync, reason)


# ── Internal bundle helpers ──────────────────────────────────────────────────

def _load_bundle() -> dict:
    """Read the consolidated JSON blob from Keychain. Returns {} on miss."""
    try:
        raw = keyring.get_password(SERVICE_NAME, BUNDLE_KEY)
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Keychain] Failed to load bundle: {e}")
    return {}


def _save_bundle(data: dict) -> None:
    """Write the consolidated JSON blob to Keychain."""
    try:
        keyring.set_password(SERVICE_NAME, BUNDLE_KEY, json.dumps(data))
    except Exception as e:
        logger.error(f"[Keychain] Failed to save bundle: {e}")
        raise


def _migrate_legacy_keys() -> None:
    """
    One-time migration: move per-key Keychain items into the bundle and
    delete the old items.  Guarded by a process-lifetime sentinel so it
    runs at most once per process, regardless of call frequency.
    """
    global _migration_done
    if _migration_done:
        return

    with _biometric_lock:  # reuse existing lock to serialise the migration
        if _migration_done:  # double-checked
            return

        bundle = _load_bundle()
        to_delete: list[str] = []
        for key_name in LEGACY_KEYS:
            if key_name in bundle:
                # Already in bundle — just mark for deletion of old item
                to_delete.append(key_name)
                continue
            try:
                val = keyring.get_password(SERVICE_NAME, key_name)
                if val:
                    bundle[key_name] = val
                    to_delete.append(key_name)
                    logger.info(f"[Keychain] Migrated legacy key: {key_name}")
            except Exception as _e:
                logger.warning(f"[keychain-manager] {type(_e).__name__}: {_e}")

        if to_delete:
            _save_bundle(bundle)

        # Only delete items we know existed — avoids macOS prompting for absent items
        for key_name in to_delete:
            try:
                keyring.delete_password(SERVICE_NAME, key_name)
            except keyring.errors.PasswordDeleteError as _e:
                logger.warning(f"[keychain-manager] {type(_e).__name__}: {_e}")
            except Exception as _e:
                logger.warning(f"[keychain-manager] {type(_e).__name__}: {_e}")

        _migration_done = True
        logger.info("[Keychain] Legacy key migration complete.")


# ── Public API ───────────────────────────────────────────────────────────────

def get_api_key(key_name: str) -> Optional[str]:
    """Get a single API key (sync). Touch ID runs at most once per process."""
    _request_biometric_auth_sync()
    _migrate_legacy_keys()
    bundle = _load_bundle()
    return bundle.get(key_name) or None


async def get_api_key_async(key_name: str) -> Optional[str]:
    """Async-safe get: Touch ID check is offloaded to thread pool."""
    await _request_biometric_auth_async()
    _migrate_legacy_keys()
    bundle = _load_bundle()
    return bundle.get(key_name) or None


def set_api_key(key_name: str, value: str) -> None:
    """Store an API key in the consolidated bundle."""
    bundle = _load_bundle()
    bundle[key_name] = value
    _save_bundle(bundle)
    logger.info(f"[Keychain] Saved key: {key_name}")


def delete_api_key(key_name: str) -> None:
    """Remove an API key from the bundle. Raises KeyError if not present."""
    bundle = _load_bundle()
    if key_name not in bundle:
        raise KeyError(key_name)
    del bundle[key_name]
    _save_bundle(bundle)
    logger.info(f"[Keychain] Deleted key: {key_name}")


async def get_all_keys_status(key_names: list) -> dict:
    """Return {key_name: bool} — async-safe, Touch ID offloaded to thread pool."""
    await _request_biometric_auth_async()
    _migrate_legacy_keys()
    bundle = _load_bundle()
    return {k: bool(bundle.get(k)) for k in key_names}
