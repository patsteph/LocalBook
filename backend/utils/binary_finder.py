"""Resolve external binary paths for bundled app environments.

When LocalBook runs as a bundled Tauri app, the process PATH is typically
restricted to /usr/bin:/bin:/usr/sbin:/sbin. Homebrew binaries at
/opt/homebrew/bin (Apple Silicon) or /usr/local/bin (Intel) won't be found
by bare name.

This module checks well-known installation paths first, then falls back
to shutil.which() for standard PATH resolution.
"""
import shutil
from typing import Optional

# Common locations for Homebrew and system binaries on macOS
_EXTRA_SEARCH_PATHS = [
    "/opt/homebrew/bin",      # Apple Silicon Homebrew
    "/usr/local/bin",         # Intel Homebrew / manual installs
    "/opt/local/bin",         # MacPorts
]


def find_binary(name: str) -> Optional[str]:
    """Find the full path to a binary, searching Homebrew paths first.

    Returns the absolute path if found, or None if the binary is not installed.

    Usage:
        ffmpeg = find_binary("ffmpeg")
        if ffmpeg:
            subprocess.run([ffmpeg, "-version"], ...)
    """
    # 1. Check well-known Homebrew / system paths first
    for directory in _EXTRA_SEARCH_PATHS:
        candidate = f"{directory}/{name}"
        if shutil.which(candidate) is not None:
            return candidate

    # 2. Fall back to standard PATH lookup
    return shutil.which(name)
