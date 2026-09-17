"""Companions — local tools LocalBook can install, connect to, and learn from.

A companion is a separate program on the user's Mac that LocalBook knows how to
find, configure, and take input from. The first is Meeting Notes
(github.com/kvango/Meeting-Summarizer), a local meeting recorder.

**Manifest-driven on purpose.** Everything specific to a tool — where its binary
lives, what directory it writes into, which config keys we own, how to ask
whether it is running — is data in `backend/companions/*.json`, not code. Adding
a second tool is a new JSON file. That is the whole reason this exists as an
architecture rather than a special case: the alternative is a `if tool ==
"meeting-notes"` branch that the third tool would have to be rewritten around.

A manifest may declare four capabilities, each optional:

  detect      how to tell whether it is installed
  install     how to install it (may require a terminal — see below)
  produces    what it writes, and which parser reads it
  configure   which of its settings LocalBook owns
  control     how to start/stop it and read its running state

**What LocalBook writes, and what it does not.** `configure.keys` is an explicit
allow-list. We rewrite exactly those keys and leave every other line of the
user's config untouched, including comments. A companion's config belongs to the
companion; we are a guest in it.

**Why the key is separate from the app token.** The app token rotates on every
launch, so it cannot be written into a config file once. Companions get a
long-lived key instead, scoped to `/v1` alone — a strictly narrower grant, and
revocable without disturbing the app.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

_MANIFEST_DIR = Path(__file__).resolve().parent.parent / "companions"
_KEY_FILE = "companion_key"


# ── the companion key ───────────────────────────────────────────────────────

def _key_path() -> Path:
    return Path(settings.data_dir) / _KEY_FILE


def get_companion_key(create: bool = True) -> Optional[str]:
    """The long-lived key companions use against `/v1`. 0600, created on demand."""
    p = _key_path()
    try:
        if p.exists():
            key = p.read_text().strip()
            if key:
                return key
        if not create:
            return None
        key = "lb-" + secrets.token_urlsafe(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key.encode("ascii"))
        finally:
            os.close(fd)
        logger.info("[companions] issued a new companion key")
        return key
    except Exception as e:
        logger.warning(f"[companions] could not read/create companion key: {e}")
        return None


def verify_companion_key(provided: str) -> bool:
    import hmac
    expected = get_companion_key(create=False)
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def revoke_companion_key() -> None:
    """Invalidate every connected companion at once. The next `/v1` call fails,
    and reconnecting reissues — which is the point of it being separate."""
    try:
        _key_path().unlink(missing_ok=True)
        logger.info("[companions] companion key revoked")
    except Exception as e:
        logger.warning(f"[companions] revoke failed: {e}")


# ── manifests ───────────────────────────────────────────────────────────────

@dataclass
class Companion:
    manifest: Dict[str, Any]

    @property
    def id(self) -> str:
        return self.manifest.get("id", "")

    def path(self, key_path: str) -> Optional[Path]:
        raw = self.manifest
        for part in key_path.split("."):
            raw = (raw or {}).get(part) if isinstance(raw, dict) else None
        return Path(str(raw)).expanduser() if raw else None


def load_manifests() -> List[Dict[str, Any]]:
    out = []
    if not _MANIFEST_DIR.is_dir():
        return out
    for f in sorted(_MANIFEST_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            if data.get("id"):
                out.append(data)
        except Exception as e:
            logger.warning(f"[companions] bad manifest {f.name}: {e}")
    return out


def get_manifest(companion_id: str) -> Optional[Dict[str, Any]]:
    return next((m for m in load_manifests() if m["id"] == companion_id), None)


# ── detection ───────────────────────────────────────────────────────────────

_BREW_PATHS = ("/opt/homebrew/bin", "/usr/local/bin")


def _which(binary: str) -> Optional[str]:
    """Homebrew's bin is not on a GUI app's inherited PATH, so a plain
    `shutil.which` reports a brew-installed tool as missing."""
    found = shutil.which(binary)
    if found:
        return found
    for d in _BREW_PATHS:
        p = Path(d) / binary
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def is_installed(manifest: Dict[str, Any]) -> bool:
    detect = manifest.get("detect") or {}
    binaries = detect.get("binaries") or []
    if binaries and all(_which(b) for b in binaries):
        return True
    support = detect.get("support_dir")
    if support and Path(support).expanduser().is_dir():
        return True
    return False


def is_running(manifest: Dict[str, Any]) -> bool:
    """Read the tool's own pidfile — the state it already publishes for its
    menu-bar plugin. A stale pidfile is treated as not running."""
    state = ((manifest.get("control") or {}).get("state") or {})
    pidfile = state.get("pidfile")
    if not pidfile:
        return False
    try:
        pid = int(Path(pidfile).expanduser().read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


# ── configuration: we own named keys, nothing else ──────────────────────────

def _substitutions() -> Dict[str, str]:
    port = getattr(settings, "api_port", 8000)
    return {
        "localbook_openai_base": f"http://127.0.0.1:{port}/v1",
        "localbook_main_model": settings.main_model or "",
        "localbook_fast_model": settings.fast_model or "",
        "companion_key": get_companion_key() or "",
    }


def _render(value: str, subs: Dict[str, str]) -> str:
    def repl(m):
        return subs.get(m.group(1), m.group(0))
    return re.sub(r"\{(\w+)\}", repl, str(value))


def desired_config(manifest: Dict[str, Any]) -> Dict[str, str]:
    cfg = manifest.get("configure") or {}
    subs = _substitutions()
    return {k: _render(v, subs) for k, v in (cfg.get("keys") or {}).items()}


def read_config(manifest: Dict[str, Any]) -> Dict[str, str]:
    """Read back only the keys we own, so 'connected?' is answerable."""
    cfg = manifest.get("configure") or {}
    f = cfg.get("file")
    if not f:
        return {}
    p = Path(f).expanduser()
    if not p.is_file():
        return {}
    wanted = set((cfg.get("keys") or {}).keys())
    out: Dict[str, str] = {}
    try:
        for line in p.read_text().splitlines():
            m = re.match(r'^\s*(?:export\s+)?(\w+)\s*=\s*"?([^"#]*)"?', line)
            if m and m.group(1) in wanted:
                out[m.group(1)] = m.group(2).strip()
    except Exception as e:
        logger.warning(f"[companions] could not read {p}: {e}")
    return out


def is_connected(manifest: Dict[str, Any]) -> bool:
    """Connected = its config actually points at us, right now."""
    desired = desired_config(manifest)
    if not desired:
        return False
    actual = read_config(manifest)
    base = desired.get("BASE_URL")
    return bool(base) and actual.get("BASE_URL") == base


def write_config(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Point the companion at LocalBook by rewriting only the keys we own.

    Every other line — the user's mic device, their email, their comments —
    survives byte for byte. A key we own that is missing gets appended; nothing
    is reordered and nothing is reformatted.
    """
    cfg = manifest.get("configure") or {}
    f = cfg.get("file")
    if not f:
        return {"ok": False, "error": "this companion has no configurable settings"}
    p = Path(f).expanduser()
    if not p.is_file():
        return {"ok": False,
                "error": f"{p} does not exist — install the tool first, then connect it."}

    desired = desired_config(manifest)
    if not desired.get("API_KEY"):
        return {"ok": False, "error": "could not issue a companion key"}

    try:
        original = p.read_text()
        lines = original.splitlines()
        remaining = dict(desired)
        out: List[str] = []
        for line in lines:
            m = re.match(r'^(\s*(?:export\s+)?)(\w+)(\s*=\s*)', line)
            if m and m.group(2) in remaining:
                key = m.group(2)
                out.append(f'{m.group(1)}{key}="{remaining.pop(key)}"')
            else:
                out.append(line)
        if remaining:
            out.append("")
            out.append("# Added by LocalBook — points this tool at LocalBook's engine.")
            for k, v in remaining.items():
                out.append(f'{k}="{v}"')

        backup = p.with_suffix(p.suffix + ".localbook-backup")
        if not backup.exists():
            backup.write_text(original)     # first write only; never clobber it
        p.write_text("\n".join(out) + "\n")
        logger.info(f"[companions] pointed {manifest['id']} at {desired.get('BASE_URL')} "
                    f"using {desired.get('LLM')}")
        return {"ok": True, "config": {k: v for k, v in desired.items() if k != "API_KEY"}}
    except Exception as e:
        logger.warning(f"[companions] could not write {p}: {e}")
        return {"ok": False, "error": f"Could not write {p}: {e}"}


def disconnect(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Restore the config we found before connecting, if we still have it."""
    cfg = manifest.get("configure") or {}
    f = cfg.get("file")
    if not f:
        return {"ok": False, "error": "nothing to restore"}
    p = Path(f).expanduser()
    backup = p.with_suffix(p.suffix + ".localbook-backup")
    if not backup.is_file():
        return {"ok": False,
                "error": "No pre-connection backup exists — edit the tool's config by hand."}
    try:
        p.write_text(backup.read_text())
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── control ─────────────────────────────────────────────────────────────────

def run_control(manifest: Dict[str, Any], action: str) -> Dict[str, Any]:
    """Start or stop the companion via the command it publishes."""
    control = manifest.get("control") or {}
    binary = control.get(action)
    if not binary:
        return {"ok": False, "error": f"'{action}' is not supported by this companion"}
    resolved = _which(binary)
    if not resolved:
        return {"ok": False, "error": f"{binary} is not installed"}
    try:
        proc = subprocess.run([resolved], capture_output=True, text=True, timeout=30)
        ok = proc.returncode == 0
        return {"ok": ok,
                "output": (proc.stdout or proc.stderr or "").strip()[:400],
                "error": None if ok else f"{binary} exited {proc.returncode}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{binary} did not return within 30s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── the view the UI renders ─────────────────────────────────────────────────

def status(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """One companion's full state. `state` is the single word the card shows."""
    installed = is_installed(manifest)
    connected = installed and is_connected(manifest)
    running = installed and is_running(manifest)

    state = "not_installed"
    if running:
        state = "recording"
    elif connected:
        state = "connected"
    elif installed:
        state = "installed"

    produces = manifest.get("produces") or {}
    out_dir = Path(produces["dir"]).expanduser() if produces.get("dir") else None

    linked_notebook = None
    if out_dir:
        try:
            from storage.folder_link_store import folder_link_store
            for link in folder_link_store.list_links():
                if Path(link["path"]) == out_dir:
                    linked_notebook = link
                    break
        except Exception as e:
            logger.debug(f"[companions] link lookup failed: {e}")

    return {
        "id": manifest["id"],
        "name": manifest.get("name", manifest["id"]),
        "author": manifest.get("author"),
        "tagline": manifest.get("tagline"),
        "description": manifest.get("description"),
        "homepage": manifest.get("homepage"),
        "icon": manifest.get("icon", "🧩"),
        "state": state,
        "installed": installed,
        "connected": connected,
        "running": running,
        "output_dir": str(out_dir) if out_dir else None,
        "output_exists": bool(out_dir and out_dir.is_dir()),
        "linked_notebook_id": (linked_notebook or {}).get("notebook_id"),
        "folder_link_id": (linked_notebook or {}).get("id"),
        "config": read_config(manifest),
        "using_model": read_config(manifest).get("LLM") if connected else None,
        "install": manifest.get("install"),
        "can_control": bool(manifest.get("control", {}).get("start")),
    }


def all_status() -> List[Dict[str, Any]]:
    return [status(m) for m in load_manifests()]
