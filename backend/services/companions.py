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
import shlex
import shutil
import subprocess
import tempfile
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


# ── outcome verification ────────────────────────────────────────────────────
#
# An installer's exit code is not evidence that it worked. Meeting Notes writes
# `brew install ffmpeg switchaudio-osx blackhole-2ch 2>/dev/null || true`, so a
# failed audio-driver install exits 0 and prints nothing — and the tool then
# records your microphone while capturing silence from the other side of every
# call. The user would not find out until they read their first set of notes.
#
# So a manifest declares what should EXIST when installation worked, and we look
# for those things. Verify the artifact, not the step.

def _audio_devices() -> List[str]:
    """Every audio device macOS currently knows about.

    CoreAudio first: it is in-process, instant, and cannot be affected by system
    load. `system_profiler` is the fallback — it spawns a process that takes
    seconds and can take much longer on a busy machine, which made it a genuine
    flake source in the test suite.

    Neither path uses `SwitchAudioSource`, even though it is simpler: that tool
    is installed BY the thing we are checking on, so it would be missing in
    exactly the failure case this function exists to detect.
    """
    try:
        from services.audio_devices import list_devices
        names = [d["name"] for d in list_devices() if d.get("name")]
        if names:
            return names
    except Exception as e:
        logger.debug(f"[companions] CoreAudio listing unavailable, falling back: {e}")

    try:
        proc = subprocess.run(
            ["system_profiler", "SPAudioDataType"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as e:
        logger.debug(f"[companions] audio device listing failed: {e}")
        return []
    names = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        # Device names sit at one indent level and end in a colon.
        if stripped.endswith(":") and 8 <= (len(line) - len(line.lstrip())) <= 10:
            name = stripped[:-1].strip()
            if name and name not in ("Devices", "Audio"):
                names.append(name)
    return names


def _check(check: Dict[str, Any], devices: Optional[List[str]] = None) -> Dict[str, Any]:
    kind = check.get("kind")
    ok = False
    detail = ""

    if kind == "binary":
        found = _which(check.get("name", ""))
        ok, detail = bool(found), found or ""
    elif kind == "path":
        p = Path(str(check.get("path", ""))).expanduser()
        ok, detail = p.exists(), str(p)
    elif kind == "audio_device":
        want = (check.get("name") or "").lower()
        names = devices if devices is not None else _audio_devices()
        match = next((n for n in names if want in n.lower()), None)
        ok, detail = bool(match), match or ""
    else:
        # An unknown check kind must not silently pass — that would be a
        # manifest typo quietly reporting a healthy install.
        return {"ok": False, "label": check.get("label", kind or "unknown check"),
                "detail": f"unknown check kind '{kind}'",
                "fix": "This companion's manifest is malformed."}

    return {"ok": ok, "kind": kind, "label": check.get("label", check.get("name", "")),
            "detail": detail, "fix": None if ok else check.get("fix")}


def verify_install(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Did the install actually produce a working tool?

    Returns every check, not just failures, so the card can show what IS working
    when something is missing — "recording works, it just can't hear the far
    side" is a far more useful thing to be told than "install failed".
    """
    checks = manifest.get("verify") or []
    if not checks:
        return {"checked": False, "ok": is_installed(manifest), "checks": []}

    # One `system_profiler` call, shared — it takes a couple of seconds.
    devices = _audio_devices() if any(c.get("kind") == "audio_device" for c in checks) else []
    results = [_check(c, devices) for c in checks]
    failed = [r for r in results if not r["ok"]]
    return {
        "checked": True,
        "ok": not failed,
        "checks": results,
        "failed_count": len(failed),
        "summary": ("Everything checks out." if not failed else
                    f"{len(failed)} of {len(results)} checks failed."),
    }


# ── install source: pinned, and verifiable before it runs ───────────────────

def install_source(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """What would actually be executed, and where it came from.

    Surfaced to the user BEFORE they agree to run it. A one-click `curl | bash`
    from a GUI app is the classic supply-chain shape; the least we can do is
    pin a commit, publish the hash, and link the exact revision so it can be
    read first.
    """
    install = manifest.get("install") or {}
    src = install.get("source") or {}
    return {
        "kind": install.get("kind"),
        "repo": src.get("repo"),
        "ref": src.get("ref"),
        "short_ref": (src.get("ref") or "")[:7],
        "ref_date": src.get("ref_date"),
        "url": src.get("url"),
        "sha256": src.get("sha256"),
        "review_url": src.get("review_url"),
        "command": install_command(manifest),
        "requires": install.get("requires") or [],
        "notes": install.get("notes") or [],
        "interactive": bool(install.get("interactive")),
    }


def install_command(manifest: Dict[str, Any]) -> str:
    """The command a user can run themselves.

    Fetches the pinned revision, checks its hash, and only then executes. The
    hash check is the whole point — without it, pinning a commit still trusts
    whatever the CDN hands back.
    """
    install = manifest.get("install") or {}
    src = install.get("source") or {}
    url, digest = src.get("url"), src.get("sha256")
    if not url:
        return install.get("command", "")
    if not digest:
        return f"curl -fsSL {url} | bash"
    return (
        f'f=$(mktemp) && curl -fsSL "{url}" -o "$f" && '
        f'echo "{digest}  $f" | shasum -a 256 -c - && bash "$f"; rm -f "$f"'
    )


def fetch_and_verify_script(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Download the pinned installer and check it against the recorded hash.

    Run before offering to execute anything. A mismatch means the pinned
    revision no longer hashes to what this manifest was written against —
    which is either upstream force-pushing a tag or something worse, and in
    both cases the answer is to stop rather than to run it.
    """
    src = (manifest.get("install") or {}).get("source") or {}
    url, expected = src.get("url"), src.get("sha256")
    if not url or not expected:
        return {"ok": False, "error": "This companion has no pinned installer to verify."}
    try:
        import hashlib
        import urllib.request
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read()
    except Exception as e:
        return {"ok": False, "error": f"Could not download the installer: {e}"}

    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        logger.warning(f"[companions] checksum mismatch for {manifest.get('id')}: "
                       f"expected {expected[:12]}… got {actual[:12]}…")
        return {"ok": False, "mismatch": True, "expected": expected, "actual": actual,
                "error": "The installer does not match the version LocalBook pinned. "
                         "It has not been run. Do not proceed until this is explained."}
    return {"ok": True, "bytes": len(body), "sha256": actual}


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


# ── preflight: everything privileged, in ONE authorization ──────────────────
#
# The goal is a single native password prompt, and LocalBook never seeing the
# password. What makes that possible is that only a small part of the install
# actually needs root:
#
#   formulae (ffmpeg, switchaudio-osx)  → user-owned brew prefix, NO password
#   the cask's .pkg                     → downloaded as the user (brew verifies
#                                         its own checksum), installed as root
#   killall coreaudiod                  → root
#
# Homebrew refuses to run as root — `check-run-command-as-root` in brew.sh — so
# wrapping the whole installer in an admin prompt is not an option, and neither
# is pre-authorising sudo on brew's behalf. Splitting it this way is what lets
# the privileged part collapse into one `do shell script … with administrator
# privileges`, which is the OS's own dialog.
#
# After this runs, the companion's own installer finds everything present. Every
# line of it that would have needed a password is written `|| true`, so it
# shrugs and carries on — without us modifying a byte of their repository.

def _brew() -> Optional[str]:
    return _which("brew")


def preflight_plan(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """What preparation is still outstanding, and which parts need a password."""
    pre = manifest.get("preflight") or {}
    if not pre:
        return {"needed": False, "steps": []}

    devices = None
    steps: List[Dict[str, Any]] = []

    for f in pre.get("formulae") or []:
        steps.append({
            "id": f"formula:{f['name']}", "label": f["name"], "why": f.get("why", ""),
            "needs_admin": False, "done": bool(_which(f.get("binary") or f["name"])),
        })

    for c in pre.get("pkg_casks") or []:
        want = c.get("audio_device")
        if want:
            if devices is None:
                devices = _audio_devices()
            done = any(want.lower() in d.lower() for d in devices)
        else:
            done = _cask_installed(c["cask"])
        steps.append({
            "id": f"cask:{c['cask']}", "label": c.get("label", c["cask"]),
            "why": c.get("why", ""), "needs_admin": True, "done": done,
        })

    mo = (pre.get("audio_setup") or {}).get("multi_output")
    if mo:
        if devices is None:
            devices = _audio_devices()
        steps.append({
            "id": f"audio:{mo['name']}", "label": mo["name"], "why": mo.get("why", ""),
            # Creating an aggregate device is a user-level CoreAudio call, so
            # this rides after the password prompt without needing another.
            "needs_admin": False,
            "done": any(mo["name"].lower() in d.lower() for d in devices),
        })

    for a in pre.get("admin_commands") or []:
        # Not idempotently checkable — it is an action, not a state. It rides
        # along inside the same authorization, so it costs nothing extra.
        steps.append({
            "id": f"cmd:{a.get('label', 'command')}", "label": a.get("label", "command"),
            "why": a.get("why", ""), "needs_admin": True, "done": False,
            "incidental": True,
        })

    outstanding = [s for s in steps if not s["done"] and not s.get("incidental")]
    return {
        "needed": bool(outstanding),
        "label": pre.get("label", "Prepare"),
        "summary": pre.get("summary", ""),
        "steps": steps,
        "will_prompt": any(s["needs_admin"] for s in steps if not s["done"]),
    }


def _ensure_audio_devices(pre: Dict[str, Any], log: List[str]) -> Optional[Dict[str, Any]]:
    """Build the Multi-Output Device the companion expects, if it is missing."""
    mo = (pre.get("audio_setup") or {}).get("multi_output")
    if not mo:
        return None
    try:
        from services.audio_devices import ensure_multi_output
    except Exception as e:
        return {"ok": False, "error": f"CoreAudio unavailable: {e}"}

    result = ensure_multi_output(
        name=mo["name"], uid=mo["uid"],
        include_names=mo.get("include") or [],
        include_default_output=bool(mo.get("include_default_output", True)),
    )
    if result.get("ok"):
        log.append(result.get("message", f"{mo['name']} ready"))
    return result


def _run_as_user(args: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Run a command as the logged-in user. Never root — Homebrew refuses it."""
    env = dict(os.environ)
    env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"       # keep it quick and predictable
    env["NONINTERACTIVE"] = "1"                # brew must never wait on a prompt
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def run_preflight(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Do the preparation, asking for the password exactly once.

    Order matters: everything that can be done WITHOUT elevation happens first,
    so that if the user cancels the password prompt they are left with a
    partially prepared machine rather than nothing — and re-running picks up
    where it stopped.
    """
    pre = manifest.get("preflight") or {}
    if not pre:
        return {"ok": True, "skipped": True, "log": []}

    brew = _brew()
    if not brew:
        return {"ok": False,
                "error": "Homebrew is required. Install it from https://brew.sh, then try again."}

    log: List[str] = []

    # ── 1. formulae — user-owned prefix, no password ────────────────────
    for f in pre.get("formulae") or []:
        name, binary = f["name"], (f.get("binary") or f["name"])
        if _which(binary):
            log.append(f"{name} already present")
            continue
        try:
            proc = _run_as_user([brew, "install", name])
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Installing {name} took too long.", "log": log}
        if not _which(binary):
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            return {"ok": False, "error": f"Could not install {name}. {tail}", "log": log}
        log.append(f"installed {name}")

    # ── 2. download the .pkg as the USER; brew verifies its own checksum ─
    pkgs: List[str] = []
    for c in pre.get("pkg_casks") or []:
        cask = c["cask"]
        want = c.get("audio_device")
        if want and any(want.lower() in d.lower() for d in _audio_devices()):
            log.append(f"{c.get('label', cask)} already installed")
            continue
        try:
            _run_as_user([brew, "fetch", "--cask", cask], timeout=600)
            proc = _run_as_user([brew, "--cache", "--cask", cask], timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Downloading {cask} took too long.", "log": log}
        path = (proc.stdout or "").strip()
        if not path or not Path(path).is_file():
            return {"ok": False,
                    "error": f"Could not download {c.get('label', cask)}.", "log": log}
        pkgs.append(path)
        log.append(f"downloaded {c.get('label', cask)}")

    admin_cmds = [a for a in (pre.get("admin_commands") or [])]
    if not pkgs and not admin_cmds:
        return {"ok": True, "log": log, "prompted": False}

    # ── 3. ONE authorization for everything privileged ──────────────────
    script_lines = ["#!/bin/sh", "set -e"]
    for pkg in pkgs:
        script_lines.append(f'/usr/sbin/installer -pkg {shlex.quote(pkg)} -target /')
    for a in admin_cmds:
        cmd = a.get("cmd", "")
        # Optional steps must not abort the rest — a Core Audio restart failing
        # is cosmetic, an aborted driver install is not.
        script_lines.append(f"{cmd} || true" if a.get("optional") else cmd)

    workdir = tempfile.mkdtemp(prefix="localbook-preflight-")   # 0700, ours alone
    script_path = Path(workdir) / "preflight.sh"
    try:
        script_path.write_text("\n".join(script_lines) + "\n")
        os.chmod(script_path, 0o700)
        # `do shell script … with administrator privileges` is the OS's own
        # authorization dialog. LocalBook never sees or handles the password.
        applescript = (
            f'do shell script "/bin/sh {shlex.quote(str(script_path))}" '
            f'with administrator privileges'
        )
        proc = subprocess.run(["/usr/bin/osascript", "-e", applescript],
                              capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The privileged step took too long.", "log": log}
    except Exception as e:
        return {"ok": False, "error": f"Could not run the privileged step: {e}", "log": log}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "User canceled" in err or "-128" in err:
            return {"ok": False, "cancelled": True, "log": log,
                    "error": "Password prompt cancelled — nothing was installed. "
                             "The steps that needed no password are done."}
        return {"ok": False, "log": log,
                "error": f"The privileged step failed: {err[-300:] or 'unknown error'}"}

    log.append("installed the audio driver")

    # ── 4. the Multi-Output Device — no password, and last ──────────────
    # Last because it can only be built once BlackHole exists, and the upstream
    # installer punts on this entirely ("can't be safely scripted"), leaving the
    # user to construct it by hand in Audio MIDI Setup. Doing it here turns that
    # dialog into a rubber stamp.
    audio_result = _ensure_audio_devices(pre, log)
    if audio_result and not audio_result.get("ok"):
        # Non-fatal: everything else installed, and the device can still be made
        # by hand. Say so rather than failing the whole preparation.
        logger.warning(f"[companions] audio device not created: {audio_result.get('error')}")

    # Outcome, not exit code — the same rule as everywhere else here.
    remaining = [s for s in preflight_plan(manifest)["steps"]
                 if not s["done"] and not s.get("incidental")]
    return {"ok": not remaining, "log": log, "prompted": True,
            "error": None if not remaining else
                     f"Still missing: {', '.join(s['label'] for s in remaining)}"}


# ── extras: optional add-ons a companion offers ─────────────────────────────
#
# Meeting Notes ships an optional SwiftBar plugin that puts a microphone icon in
# the menu bar and turns it red while recording. It is genuinely optional — the
# tool works fine without it — so it is offered rather than bundled into the
# main install, and can be removed without touching anything else.
#
# Modelled generically because the second companion will have its own optional
# pieces, and "one checkbox per add-on" should not need new code each time.
#
# Unlike the main installer, an extra needs NO privilege: SwiftBar is an `app`
# cask (no pkg, no admin) and the plugin is a shell script we place in a folder
# the user already owns. This is the one part of the flow that really is
# one click.

def _extra(manifest: Dict[str, Any], extra_id: str) -> Optional[Dict[str, Any]]:
    return next((e for e in (manifest.get("extras") or []) if e.get("id") == extra_id), None)


def _plugin_dir(extra: Dict[str, Any]) -> Path:
    """Where the host app expects its plugins.

    SwiftBar's folder is chosen by the user on first launch and recorded in its
    preferences, so read that first — writing to our guess when they picked
    somewhere else would install a plugin that never loads and looks broken.
    """
    install = extra.get("install") or {}
    pref = install.get("dir_pref") or {}
    domain, key = pref.get("domain"), pref.get("key")
    if domain and key:
        try:
            proc = subprocess.run(["defaults", "read", domain, key],
                                  capture_output=True, text=True, timeout=10)
            value = (proc.stdout or "").strip()
            if proc.returncode == 0 and value:
                return Path(value).expanduser()
        except Exception as e:
            logger.debug(f"[companions] could not read {domain}.{key}: {e}")
    return Path(str(install.get("dir_default", "~"))).expanduser()


def _extra_target(extra: Dict[str, Any]) -> Path:
    install = extra.get("install") or {}
    return _plugin_dir(extra) / str(install.get("filename", "plugin.sh"))


def _cask_installed(cask: str) -> bool:
    """An app cask is present if its .app is on disk — cheaper and more honest
    than shelling out to brew, which is slow and may not even be on PATH."""
    if not cask:
        return True
    name = {"swiftbar": "SwiftBar"}.get(cask, cask)
    return any((Path(d) / f"{name}.app").is_dir()
               for d in ("/Applications", str(Path.home() / "Applications")))


def extras_status(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for e in manifest.get("extras") or []:
        target = _extra_target(e)
        host_ok = _cask_installed(e.get("requires_cask", ""))
        out.append({
            "id": e.get("id"),
            "name": e.get("name"),
            "tagline": e.get("tagline"),
            "description": e.get("description"),
            "notes": e.get("notes") or [],
            "review_url": (e.get("source") or {}).get("review_url"),
            "installed": target.is_file(),
            "host_installed": host_ok,
            "host_cask": e.get("requires_cask"),
            "host_needs_admin": bool(e.get("cask_needs_admin")),
            "target": str(target),
        })
    return out


def install_extra(manifest: Dict[str, Any], extra_id: str) -> Dict[str, Any]:
    """Install one optional add-on: fetch the pinned file, check its hash, place it.

    Same rule as the main installer — the checksum is verified BEFORE anything
    is written, not after.
    """
    extra = _extra(manifest, extra_id)
    if not extra:
        return {"ok": False, "error": "Unknown add-on"}

    cask = extra.get("requires_cask", "")
    if cask and not _cask_installed(cask):
        found = _which("brew")
        if not found:
            return {"ok": False, "error": f"Homebrew is needed to install {cask}."}
        try:
            proc = subprocess.run([found, "install", "--cask", cask],
                                  capture_output=True, text=True, timeout=600)
            if not _cask_installed(cask):
                tail = (proc.stderr or proc.stdout or "").strip()[-300:]
                return {"ok": False,
                        "error": f"Could not install {cask}. {tail}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Installing {cask} took too long."}
        except Exception as e:
            return {"ok": False, "error": f"Could not install {cask}: {e}"}

    src = extra.get("source") or {}
    url, expected = src.get("url"), src.get("sha256")
    if not url or not expected:
        return {"ok": False, "error": "This add-on has no pinned source."}
    try:
        import hashlib
        import urllib.request
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read()
    except Exception as e:
        return {"ok": False, "error": f"Could not download the add-on: {e}"}

    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        logger.warning(f"[companions] extra checksum mismatch for {extra_id}")
        return {"ok": False, "mismatch": True,
                "error": "The add-on does not match the version LocalBook pinned. "
                         "Nothing was installed."}

    target = _extra_target(extra)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        os.chmod(target, int((extra.get("install") or {}).get("mode", 0o755)))
    except Exception as e:
        return {"ok": False, "error": f"Could not write {target}: {e}"}

    logger.info(f"[companions] installed add-on {extra_id} → {target}")
    return {"ok": True, "target": str(target),
            "host_installed": _cask_installed(cask)}


def remove_extra(manifest: Dict[str, Any], extra_id: str) -> Dict[str, Any]:
    """Remove the add-on. Deliberately leaves its host app alone — the user may
    be running other SwiftBar plugins, and uninstalling SwiftBar to remove one
    plugin would be destroying something we did not create."""
    extra = _extra(manifest, extra_id)
    if not extra:
        return {"ok": False, "error": "Unknown add-on"}
    target = _extra_target(extra)
    try:
        target.unlink(missing_ok=True)
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
        "install": install_source(manifest),
        "can_control": bool(manifest.get("control", {}).get("start")),
        "has_checks": bool(manifest.get("verify")),
        "extras": extras_status(manifest),
        "preflight": preflight_plan(manifest),
    }


def all_status() -> List[Dict[str, Any]]:
    return [status(m) for m in load_manifests()]
