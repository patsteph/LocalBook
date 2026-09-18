"""A clean environment for programs we launch.

A PyInstaller-bundled app is a hostile parent process. Two kinds of variable
leak into every child and quietly break it:

1. **PyInstaller's loader variables.** The bootloader points `DYLD_LIBRARY_PATH`
   at the bundle so our own binary finds its libraries. A child process that
   inherits it tries to load OUR copies of libssl, libcrypto and friends.
   PyInstaller saves the real values as `<VAR>_ORIG` precisely so they can be
   restored; this does that.

2. **Our TLS overrides.** `main.py` sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`
   and `CURL_CA_BUNDLE` to the certifi bundle inside the app, which is right for
   our own HTTP clients and wrong for everyone else's.

   That second one caused a real failure (2026-09-18). Homebrew uses `curl`, and
   inherited `CURL_CA_BUNDLE` pointing at a certifi-only bundle. On a network
   that inspects HTTPS — re-signing with a private root macOS trusts and certifi
   has never heard of — brew could not fetch bottle manifests, decided no bottle
   existed, and told the user to build from source on an "unsupported
   configuration". The formula was fine; our environment was not.

Only values that point INSIDE our own bundle are removed. A cert bundle the user
set themselves is theirs, and is left exactly as it is.
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

# Loader variables the PyInstaller bootloader rewrites. It stashes the original
# in `<VAR>_ORIG`, so restoring is exact rather than guesswork.
_LOADER_VARS = (
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "DYLD_INSERT_LIBRARIES",
    "LD_LIBRARY_PATH",
    "LIBPATH",
)

# Ours, and meaningless-or-harmful to anyone else.
_TLS_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
_PYTHON_VARS = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "_MEIPASS2")

# Homebrew's bin is not on a GUI app's inherited PATH.
_EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
               "/usr/sbin", "/sbin")


def _bundle_roots() -> List[str]:
    """Directories that belong to this app, used to tell our values from theirs."""
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(str(meipass))
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(sys.executable))
    return [r for r in roots if r]


def _is_ours(value: Optional[str]) -> bool:
    if not value:
        return False
    return any(value.startswith(root) for root in _bundle_roots())


def clean_child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment an external program should see, as if launched from a shell."""
    env = dict(os.environ)

    for var in _LOADER_VARS:
        original = env.pop(f"{var}_ORIG", None)
        if original:
            env[var] = original
        else:
            env.pop(var, None)

    for var in _PYTHON_VARS:
        env.pop(var, None)

    for var in _TLS_VARS:
        if _is_ours(env.get(var)):
            env.pop(var, None)

    parts = [p for p in env.get("PATH", "").split(":") if p]
    for p in _EXTRA_PATH:
        if p not in parts:
            parts.append(p)
    env["PATH"] = ":".join(parts)

    if extra:
        env.update(extra)
    return env
