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
import os
import threading
import time
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

GATED_KEYS = frozenset({"brave_api_key", "youtube_api_key"})  # search + YouTube only
# How long a Touch ID unlock stays valid (seconds). Generous by default so
# background search/collection within a work session doesn't re-prompt.
_BIOMETRIC_TTL = float(os.getenv("LOCALBOOK_KEYCHAIN_BIOMETRIC_TTL", "14400"))  # 4h

_biometric_lock = threading.Lock()
_biometric_until = 0.0             # monotonic deadline: unlock valid until this time
_migration_done = False            # Sentinel: legacy migration runs exactly once


def _request_biometric_auth_sync(reason: str = "unlock LocalBook search & YouTube keys") -> bool:
    """Touch ID gate for the search/YouTube keys (TTL-cached session).

    Returns True when access is granted: the user authenticated with Touch ID
    and is still within the TTL window, OR biometrics aren't available on this
    Mac (we can't gate, so we never lock the user out). Returns False ONLY when
    biometrics ARE available and the user failed/cancelled — the caller then
    withholds the key and the feature degrades gracefully.

    Runs the blocking LAContext evaluation on the calling (worker) thread; the
    async wrapper offloads it so the event loop never blocks.
    """
    global _biometric_until

    if time.monotonic() < _biometric_until:
        return True

    with _biometric_lock:
        if time.monotonic() < _biometric_until:  # double-checked
            return True

        try:
            from LocalAuthentication import (  # type: ignore  # pyobjc
                LAContext,
                LAPolicyDeviceOwnerAuthentication,
            )

            ctx = LAContext.new()
            # DeviceOwnerAuthentication = Touch ID where available, else the device
            # password — so a non-Touch-ID Mac still gets a local-auth gate, not a
            # free pass. (Also the password is the fallback if Touch ID fails.)
            can_eval, _ = ctx.canEvaluatePolicy_error_(
                LAPolicyDeviceOwnerAuthentication, None
            )
            if not can_eval:
                # No auth method at all (no device password set / headless) — can't
                # gate; grant + cache so we don't re-probe on every read.
                logger.debug("[Keychain] Local auth unavailable — skipping gate.")
                _biometric_until = time.monotonic() + _BIOMETRIC_TTL
                return True

            result_holder: list[bool] = [False]
            done = threading.Event()

            def _handler(success, error) -> None:  # error is NSError | None
                result_holder[0] = bool(success)
                done.set()

            ctx.evaluatePolicy_localizedReason_reply_(
                LAPolicyDeviceOwnerAuthentication,
                reason,
                _handler,
            )
            # Block *this* thread (not the event loop) waiting for the OS callback
            done.wait(timeout=30)

            if result_holder[0]:
                _biometric_until = time.monotonic() + _BIOMETRIC_TTL
                logger.info(
                    f"[Keychain] Authenticated (Touch ID or password) — "
                    f"valid for {_BIOMETRIC_TTL / 3600:.1f}h."
                )
                return True

            logger.info("[Keychain] Auth failed/cancelled — search/YouTube keys stay locked.")
            return False

        except ImportError:
            # pyobjc LocalAuthentication not bundled (non-Mac / missing) — can't gate.
            logger.debug("[Keychain] LocalAuthentication unavailable — biometric gate skipped.")
            _biometric_until = time.monotonic() + _BIOMETRIC_TTL
            return True
        except Exception as e:
            # Never lock the user out on an unexpected error — grant + cache.
            logger.warning(f"[Keychain] Biometric check error (non-fatal, granting): {e}")
            _biometric_until = time.monotonic() + _BIOMETRIC_TTL
            return True


async def _request_biometric_auth_async(reason: str = "unlock LocalBook search & YouTube keys") -> bool:
    """Async-safe wrapper: runs the blocking Touch ID check in a thread pool."""
    if time.monotonic() < _biometric_until:
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
    """Get a single API key (sync). Search/YouTube keys are Touch ID-gated."""
    if key_name in GATED_KEYS and not _request_biometric_auth_sync():
        logger.info(f"[Keychain] {key_name} withheld — Touch ID not satisfied.")
        return None
    _migrate_legacy_keys()
    bundle = _load_bundle()
    return bundle.get(key_name) or None


async def get_api_key_async(key_name: str) -> Optional[str]:
    """Async-safe get: search/YouTube keys are Touch ID-gated (offloaded)."""
    if key_name in GATED_KEYS and not await _request_biometric_auth_async():
        logger.info(f"[Keychain] {key_name} withheld — Touch ID not satisfied.")
        return None
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
    """Return {key_name: bool} presence map — no Touch ID (no secret values returned)."""
    _migrate_legacy_keys()
    bundle = _load_bundle()
    return {k: bool(bundle.get(k)) for k in key_names}
