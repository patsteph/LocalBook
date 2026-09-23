"""The running app's version.

DERIVED, not declared. This file said 2.1.1 while every other manifest said
2.3.0 — it is the backend's only version source, it feeds
`/updates/startup-status`, and so the splash screen showed a version two
releases stale. A constant that must be edited in lockstep with four other
files will eventually not be, and was not.

Order of authority:
  1. The bundle's own Info.plist — what macOS believes this app is. Correct by
     construction in a packaged build, because Tauri wrote it.
  2. package.json / tauri.conf.json — correct in a dev checkout.
  3. `_FALLBACK` — only reached if both are missing, and release.sh keeps it
     current so even that is right.
"""
from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path
from typing import Optional

_FALLBACK = "2.4.0"
DATA_SCHEMA_VERSION = "0.6.5"


def _from_bundle() -> Optional[str]:
    """CFBundleShortVersionString, when running inside a .app.

    The sidecar lives at Contents/Resources/resources/backend/<name>/, so the
    Info.plist is a few levels up. Bounded walk, never raises.
    """
    try:
        start = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
        for parent in list(start.parents)[:8]:
            if parent.name == "Contents":
                plist = parent / "Info.plist"
                if plist.is_file():
                    data = plistlib.loads(plist.read_bytes())
                    v = (data.get("CFBundleShortVersionString")
                         or data.get("CFBundleVersion") or "").strip()
                    if v:
                        return v
    except Exception:
        pass
    return None


def _from_manifest() -> Optional[str]:
    """package.json or tauri.conf.json, walking up from here (dev checkout)."""
    try:
        for parent in list(Path(__file__).resolve().parents)[:6]:
            for name in ("package.json", "src-tauri/tauri.conf.json"):
                f = parent / name
                if f.is_file():
                    v = (json.loads(f.read_text()).get("version") or "").strip()
                    if v:
                        return v
    except Exception:
        pass
    return None


APP_VERSION = _from_bundle() or _from_manifest() or _FALLBACK
