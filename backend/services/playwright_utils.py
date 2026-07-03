"""Shared Playwright browser utilities.

Ensures Playwright always finds Chromium at the system cache location,
not inside a PyInstaller bundle — AND (S3/C4, 2026-07-03) owns the ONE
shared headless chromium used by every PNG renderer (svg_renderer,
mermaid_renderer, video_slide_renderer). Previously svg + mermaid each
cached their own module-global browser and video launched a fresh one per
render — 2-3 chromium processes where one suffices (a real memory win on
the 16 GB floor). social_auth keeps its own VISIBLE browser; web_scraper
keeps ephemeral launches (isolation is a feature for long scrapes).
"""

import asyncio
import logging
import os
import stat
import subprocess
import sys
from pathlib import Path
logger = logging.getLogger(__name__)

_log = logging.getLogger(__name__)


def ensure_playwright_browsers_path():
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
    except Exception as _e:
        logger.debug(f"[playwright-utils] {type(_e).__name__}: {_e}")

    _log.error(
        "[Playwright] All auto-install strategies failed. "
        "Please run manually: playwright install chromium"
    )


# ── Shared headless browser (S3/C4) ─────────────────────────────────────────
_shared_browser = None
_shared_lock = None  # created lazily so it binds to the running loop


def _get_lock():
    global _shared_lock
    if _shared_lock is None:
        _shared_lock = asyncio.Lock()
    return _shared_lock


async def get_shared_browser():
    """Return the process-wide headless chromium, launching it on first use.
    Returns None on launch failure (callers fall back / degrade)."""
    global _shared_browser
    async with _get_lock():
        if _shared_browser is not None and _shared_browser.is_connected():
            return _shared_browser
        try:
            from playwright.async_api import async_playwright
            ensure_playwright_browsers_path()
            pw = await async_playwright().start()
            _shared_browser = await pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
            )
            _log.info("[playwright_utils] shared headless browser launched")
            return _shared_browser
        except Exception as e:
            _log.error(f"[playwright_utils] shared browser launch failed: {e}")
            _shared_browser = None
            return None


async def shutdown_shared_browser():
    """Close the shared browser. Wired into main.py lifespan shutdown."""
    global _shared_browser
    if _shared_browser is not None:
        try:
            await _shared_browser.close()
        except Exception as e:
            _log.debug(f"[playwright_utils] shared browser shutdown warn: {e}")
        _shared_browser = None
