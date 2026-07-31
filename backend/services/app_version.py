"""App version + environment facts — a small, reusable, never-raise helper.

Quality Signals Phase 2 (slice 2a) needs a stable way to stamp an incident with
"which build / which machine produced this near-miss" WITHOUT any of the noisy,
privacy-sensitive detail `health_portal` collects (no memory/disk numbers, no
data_dir path). This module is that thin, allowlist-shaped fact source.

Guarantees:
  - `get_app_version_info()` NEVER raises — every field degrades to a safe default.
  - No network, no subprocess that can hang (git call is short-timeout + optional).
  - Pure enough to unit-test: shape is fixed, values are best-effort.

Reused by `incident_context.build_incident_context()` and (later) `GET /system/version`,
which is wired inline by the supervisor — this module only exposes the helper.
"""
from __future__ import annotations

import logging
import platform
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_UNKNOWN = "unknown"


def _read_package_version() -> str:
    """Read the app version from package.json / tauri.conf.json by walking up.

    In a dev checkout these live at the repo root (a few dirs above this file). In a
    PyInstaller build they may be absent — we fall back to "unknown" rather than raise.
    """
    import json

    here = Path(__file__).resolve()
    # Walk up a bounded number of parents looking for a version-bearing manifest.
    for parent in list(here.parents)[:6]:
        for name in ("package.json", "src-tauri/tauri.conf.json"):
            candidate = parent / name
            try:
                if candidate.is_file():
                    data = json.loads(candidate.read_text())
                    ver = data.get("version")
                    if isinstance(ver, str) and ver.strip():
                        return ver.strip()
            except Exception:
                continue
    return _UNKNOWN


def _read_git_sha() -> Optional[str]:
    """Best-effort short git SHA. Returns None if not a repo / git unavailable.

    Short timeout so a slow/hung git can never stall the caller.
    """
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(here),
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if out.returncode == 0:
            sha = out.stdout.strip()
            if sha:
                return sha
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def get_app_version_info() -> Dict[str, Any]:
    """Return a fixed-shape dict of build + platform facts. Never raises.

    Shape (all keys always present):
        {
          "app_version": str,          # e.g. "2.1.1" or "unknown"
          "git_sha": str | None,       # short SHA or None outside a repo
          "platform": str,             # e.g. "Darwin"
          "platform_release": str,     # OS release string (best-effort)
          "arch": str,                 # e.g. "arm64"
          "python_version": str,       # e.g. "3.13.1"
        }
    Cached — build facts do not change during a process lifetime.
    """
    info: Dict[str, Any] = {
        "app_version": _UNKNOWN,
        "git_sha": None,
        "platform": _UNKNOWN,
        "platform_release": _UNKNOWN,
        "arch": _UNKNOWN,
        "python_version": _UNKNOWN,
    }
    try:
        info["app_version"] = _read_package_version()
    except Exception:
        pass
    try:
        info["git_sha"] = _read_git_sha()
    except Exception:
        pass
    try:
        info["platform"] = platform.system() or _UNKNOWN
    except Exception:
        pass
    try:
        # mac_ver()[0] on Darwin gives the clean "14.5"-style release; fall back to release().
        rel = platform.mac_ver()[0] if platform.system() == "Darwin" else ""
        info["platform_release"] = rel or platform.release() or _UNKNOWN
    except Exception:
        pass
    try:
        info["arch"] = platform.machine() or _UNKNOWN
    except Exception:
        pass
    try:
        info["python_version"] = platform.python_version() or _UNKNOWN
    except Exception:
        pass
    return info
