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
from datetime import datetime
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
        from utils.subprocess_env import clean_child_env
        proc = subprocess.run(
            ["system_profiler", "SPAudioDataType"],
            capture_output=True, text=True, timeout=20, env=clean_child_env(),
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
    src = _effective_pin(manifest.get("id", ""), "install", install.get("source") or {})
    return {
        "kind": install.get("kind"),
        "user_accepted": src.get("user_accepted", False),
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
    src = _effective_pin(manifest.get("id", ""), "install", install.get("source") or {})
    url, digest = src.get("url"), src.get("sha256")
    if not url:
        return install.get("command", "")
    if not digest:
        return f"curl -fsSL {url} | bash"
    # NONINTERACTIVE stops Homebrew asking "proceed? [y/n]" twice mid-install;
    # NO_AUTO_UPDATE stops it refreshing every tap and printing a page of new
    # formulae nobody asked about. Neither changes what gets installed — they
    # just stop a scripted install from stalling on a question.
    return (
        f'f=$(mktemp) && curl -fsSL "{url}" -o "$f" && '
        f'echo "{digest}  $f" | shasum -a 256 -c - && '
        f'HOMEBREW_NO_AUTO_UPDATE=1 NONINTERACTIVE=1 bash "$f"; rm -f "$f"'
    )


def run_installer(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Start the installer in Terminal, so nobody has to copy and paste it.

    **Why Terminal and not the background.** The installer needs root once, for
    the audio driver's pkg. Running it headless means giving sudo a password
    with no terminal to ask on — which in practice means `SUDO_ASKPASS` pointed
    at a dialog WE draw, and the password passing through a script we wrote.
    That is a meaningfully worse bargain than it looks:

      * macOS's own authorization dialog is recognisable and hard to forge.
        One we draw is trivially imitable, and teaching someone to type their
        admin password into a LocalBook-shaped prompt is a habit worth not
        creating.
      * The install downloads several GB and takes minutes. A progress log the
        user can watch is a feature, not a consolation — a silent spinner for
        that long is indistinguishable from a hang, which this feature has
        already been mistaken for once.

    So: one click, Terminal opens, sudo prompts natively when it needs to, and
    LocalBook watches for completion. The password never touches us.

    The command still verifies the pinned checksum before executing.
    """
    command = install_command(manifest)
    if not command:
        return {"ok": False, "error": "This companion has no installer."}

    verified = fetch_and_verify_script(manifest)
    if not verified.get("ok"):
        # Never hand a terminal a command whose payload we could not verify.
        return {"ok": False, "error": verified.get("error", "Could not verify the installer.")}

    name = manifest.get("name", manifest.get("id", "installer"))
    # A banner first, so the terminal explains itself rather than appearing to
    # be something the user's machine did on its own.
    script = (
        f'echo "Installing {name} for LocalBook."; '
        f'echo "It will ask for your password once, for the audio driver."; '
        f'echo "You can close this window when it finishes."; echo; '
        f'{command}'
    )
    try:
        # osascript rather than `open -a Terminal`: `open` with a command needs
        # a file on disk, and a script we write and they execute is a worse
        # thing to leave lying around than a line typed into a fresh window.
        applescript = (
            'tell application "Terminal"\n'
            f'  do script {json.dumps(script)}\n'
            '  activate\n'
            'end tell'
        )
        proc = subprocess.run(["/usr/bin/osascript", "-e", applescript],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return {"ok": False,
                    "error": (proc.stderr or "").strip()[:200] or "Could not open Terminal."}
    except Exception as e:
        return {"ok": False, "error": f"Could not open Terminal: {e}"}

    logger.info(f"[companions] launched the installer for {manifest.get('id')} in Terminal")
    return {"ok": True, "command": command}


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
    # A companion may declare that its preparation belongs AFTER its installer —
    # ours does, because the device it builds needs a driver the installer
    # provides. Offering it beforehand would fail every time.
    after_install = pre.get("when") == "after_install"

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
        "after_install": after_install,
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

    # Wait for the driver we just installed: the privileged step ends with
    # `killall coreaudiod`, and the device list is not repopulated instantly.
    result = ensure_multi_output(
        name=mo["name"], uid=mo["uid"],
        include_names=mo.get("include") or [],
        include_default_output=bool(mo.get("include_default_output", True)),
        wait_seconds=float(mo.get("wait_seconds", 25)),
    )
    if result.get("ok"):
        log.append(result.get("message", f"{mo['name']} ready"))
    return result


def _run_as_user(args: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Run a command as the logged-in user, in a CLEAN environment.

    Never as root — Homebrew refuses that. And never with our environment: a
    PyInstaller app leaks loader paths and TLS overrides into every child, and
    `CURL_CA_BUNDLE` pointing at our certifi bundle stopped brew from fetching
    bottle manifests on a network that inspects HTTPS. brew then reported that
    no bottle existed and suggested building from source. The formula was fine;
    what we handed it was not.
    """
    from utils.subprocess_env import clean_child_env
    env = clean_child_env({
        "HOMEBREW_NO_AUTO_UPDATE": "1",    # keep it quick and predictable
        "NONINTERACTIVE": "1",             # brew must never wait on a prompt
    })
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def _brew_error(proc: subprocess.CompletedProcess, package: str) -> str:
    """Turn Homebrew's output into one line a person can act on.

    Its failures end in a wall of support-tier boilerplate and "do not report
    issues", none of which helps. Surface the cause, not the footer.
    """
    text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    lowered = text.lower()
    if "no bottle available" in lowered or "build-from-source" in lowered:
        return (f"Homebrew has no prebuilt {package} for this macOS version. "
                f"You can install it yourself with: brew install {package}")
    if "ssl" in lowered or "certificate" in lowered or "curl" in lowered:
        return (f"Homebrew could not download {package} — the network blocked or "
                f"re-signed the connection. Try: brew install {package}")
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("error:"):
            return line[:200]
    return (text.splitlines() or [f"brew install {package} failed"])[0][:200]


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
    warnings: List[str] = []
    audio_result: Optional[Dict[str, Any]] = None

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
            message = _brew_error(proc, name)
            if f.get("required", True):
                return {"ok": False, "error": message, "log": log}
            # Optional: note it and carry on. Aborting the whole preparation —
            # including the audio driver — because one helper has no bottle
            # would be a worse outcome than a partly prepared machine.
            log.append(f"could not install {name} (optional)")
            warnings.append(message)
            continue
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

    # The incidental admin commands exist to make a NEWLY INSTALLED driver
    # appear. With nothing installed this pass there is nothing to settle, and
    # asking for a password to run `killall coreaudiod` against a machine that
    # already has the driver is a prompt the user cannot possibly want.
    admin_cmds = [a for a in (pre.get("admin_commands") or [])] if pkgs else []

    if not pkgs and not admin_cmds:
        # Still finish the job: the audio device needs no privilege, and this is
        # the exact path a machine takes when only that step remains.
        audio_result = _ensure_audio_devices(pre, log)
        if audio_result and not audio_result.get("ok"):
            logger.warning(f"[companions] audio device not created: {audio_result.get('error')}")
            warnings.append(audio_result.get("error") or "The audio device could not be created.")
        remaining = [st for st in preflight_plan(manifest)["steps"]
                     if not st["done"] and not st.get("incidental")]
        error = None
        if remaining:
            labels = ", ".join(st["label"] for st in remaining)
            error = (f"{warnings[0]} (still missing: {labels})" if warnings
                     else f"Still missing: {labels}")
        return {"ok": not remaining, "log": log, "prompted": False,
                "warnings": warnings, "error": error, "details": audio_result or {}}

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
        from utils.subprocess_env import clean_child_env
        proc = subprocess.run(["/usr/bin/osascript", "-e", applescript],
                              capture_output=True, text=True, timeout=900,
                              env=clean_child_env())
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
        # by hand. Surface it rather than burying it in the log.
        logger.warning(f"[companions] audio device not created: {audio_result.get('error')}")
        warnings.append(audio_result.get("error") or "The audio device could not be created.")

    # Outcome, not exit code — the same rule as everywhere else here.
    remaining = [s for s in preflight_plan(manifest)["steps"]
                 if not s["done"] and not s.get("incidental")]
    # Lead with WHY, not WHAT. "Still missing: Meeting Output" names the symptom
    # and drops the reason we collected two lines earlier — which is what left a
    # real failure undiagnosable on someone else's machine (2026-09-21).
    error = None
    if remaining:
        labels = ", ".join(s["label"] for s in remaining)
        error = (f"{warnings[0]} (still missing: {labels})" if warnings
                 else f"Still missing: {labels}")
    return {"ok": not remaining, "log": log, "prompted": True, "warnings": warnings,
            "error": error, "details": audio_result or {}}


# ── updates: pinned, but not frozen ─────────────────────────────────────────
#
# Pinning to a commit protects the user from a moving `curl | bash` target. It
# also freezes them: if upstream fixes a bug, nobody ever gets it. Both matter,
# so the pin is kept and updates are made VISIBLE and EXPLICIT instead of
# automatic.
#
# Two layers:
#   the shipped manifest   — the revision LocalBook reviewed and ships
#   companion_pins.json    — the revision THIS user chose to accept
#
# The overlay wins. Accepting an update is a deliberate act with the diff in
# front of you; nothing here ever advances a pin on its own.
#
# The check itself costs nothing: hashing the raw file on the default branch
# answers "did the part we actually run change?" directly, with no API call and
# no rate limit. github.com's API (60/hour unauthenticated) is touched only when
# something HAS changed, to name the new commit for provenance.

def _pins_path() -> Path:
    return Path(settings.data_dir) / "companion_pins.json"


def _load_pins() -> Dict[str, Any]:
    try:
        p = _pins_path()
        return json.loads(p.read_text()) if p.is_file() else {}
    except Exception as e:
        logger.warning(f"[companions] could not read accepted pins: {e}")
        return {}


def _save_pins(pins: Dict[str, Any]) -> None:
    try:
        p = _pins_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(pins, indent=2))
    except Exception as e:
        logger.warning(f"[companions] could not save accepted pins: {e}")


def _effective_pin(companion_id: str, artifact: str,
                   shipped: Dict[str, Any]) -> Dict[str, Any]:
    """What this user is actually running: their accepted pin, else ours."""
    accepted = ((_load_pins().get(companion_id) or {}).get(artifact) or {})
    if accepted.get("sha256") and accepted.get("url"):
        return {**shipped, **accepted, "user_accepted": True}
    return {**shipped, "user_accepted": False}


def _artifacts(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Everything of upstream's that we fetch and run, as (id, label, source)."""
    out = []
    src = (manifest.get("install") or {}).get("source")
    if src:
        out.append({"id": "install", "label": "Installer", "source": src})
    for e in manifest.get("extras") or []:
        if e.get("source"):
            out.append({"id": f"extra:{e['id']}", "label": e.get("name", e["id"]),
                        "source": e["source"], "extra_id": e["id"]})
    return out


def _raw_url(source: Dict[str, Any], ref: str) -> Optional[str]:
    repo, path = source.get("repo"), source.get("path")
    if not repo or not path:
        return None
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"


def _fetch(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logger.debug(f"[companions] fetch failed for {url}: {e}")
        return None


def _latest_ref(repo: str, branch: str) -> Optional[Dict[str, str]]:
    """Name the commit now on the branch. One API call, only when something
    already changed — so the 60/hour unauthenticated budget is never a concern."""
    body = _fetch(f"https://api.github.com/repos/{repo}/commits/{branch}", timeout=20)
    if not body:
        return None
    try:
        data = json.loads(body)
        return {
            "ref": data.get("sha", ""),
            "date": (data.get("commit", {}).get("committer", {}).get("date") or "")[:10],
            "message": (data.get("commit", {}).get("message") or "").split("\n")[0][:140],
            "author": (data.get("author") or {}).get("login") or "",
        }
    except Exception:
        return None


def check_updates(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Has anything we run actually changed upstream?

    Compares CONTENT, not commits. Upstream can move a dozen times without the
    installer changing a byte — a README edit is not an update, and nagging
    about one teaches people to dismiss the badge that matters.
    """
    cid = manifest.get("id", "")
    results: List[Dict[str, Any]] = []

    for art in _artifacts(manifest):
        shipped = art["source"]
        pin = _effective_pin(cid, art["id"], shipped)
        repo = shipped.get("repo")
        branch = shipped.get("branch") or "main"
        url = _raw_url(shipped, branch)
        entry = {
            "id": art["id"], "label": art["label"],
            "current_ref": (pin.get("ref") or "")[:7],
            "user_accepted": pin.get("user_accepted", False),
            "changed": False, "error": None,
        }
        if not url:
            entry["error"] = "no upstream source recorded"
            results.append(entry)
            continue

        body = _fetch(url)
        if body is None:
            entry["error"] = "could not reach GitHub"
            results.append(entry)
            continue

        import hashlib
        live = hashlib.sha256(body).hexdigest()
        if live == pin.get("sha256"):
            results.append(entry)
            continue

        entry["changed"] = True
        entry["new_sha256"] = live
        entry["new_url"] = url
        latest = _latest_ref(repo, branch) if repo else None
        if latest and latest.get("ref"):
            entry["new_ref"] = latest["ref"][:7]
            entry["new_ref_full"] = latest["ref"]
            entry["new_date"] = latest.get("date")
            entry["message"] = latest.get("message")
            entry["compare_url"] = (
                f"https://github.com/{repo}/compare/{pin.get('ref')}...{latest['ref']}"
                if pin.get("ref") else f"https://github.com/{repo}/commits/{branch}")
        results.append(entry)

    changed = [r for r in results if r["changed"]]
    return {
        "checked_at": datetime.utcnow().isoformat(),
        "has_updates": bool(changed),
        "artifacts": results,
        "summary": ("Up to date." if not changed else
                    f"{len(changed)} update{'s' if len(changed) != 1 else ''} available."),
    }


def _update_cache_path() -> Path:
    return Path(settings.data_dir) / "companion_updates.json"


def cached_updates(companion_id: str) -> Dict[str, Any]:
    """The last check's result, read from disk. Never touches the network.

    `status()` is called every time the Settings tab renders and by a polling
    card; doing a network round-trip there would make the UI wait on GitHub.
    The check itself runs on a schedule, or when the user asks.
    """
    try:
        p = _update_cache_path()
        if not p.is_file():
            return {"checked_at": None, "has_updates": False, "artifacts": []}
        return (json.loads(p.read_text()).get(companion_id)
                or {"checked_at": None, "has_updates": False, "artifacts": []})
    except Exception:
        return {"checked_at": None, "has_updates": False, "artifacts": []}


def check_and_cache(manifest: Dict[str, Any]) -> Dict[str, Any]:
    result = check_updates(manifest)
    try:
        p = _update_cache_path()
        all_results = json.loads(p.read_text()) if p.is_file() else {}
        all_results[manifest.get("id", "")] = result
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(all_results, indent=2))
    except Exception as e:
        logger.warning(f"[companions] could not cache update check: {e}")
    return result


def check_all_for_updates() -> Dict[str, Any]:
    """Background sweep. Only checks companions the user actually installed —
    telling someone a tool they do not have has an update is pure noise."""
    checked = {}
    for manifest in load_manifests():
        if not is_installed(manifest):
            continue
        try:
            result = check_and_cache(manifest)
            if result.get("has_updates"):
                logger.info(f"[companions] {manifest['id']}: {result['summary']}")
            checked[manifest["id"]] = result.get("summary")
        except Exception as e:
            logger.warning(f"[companions] update check failed for {manifest.get('id')}: {e}")
    return checked


def accept_update(manifest: Dict[str, Any], artifact_id: str) -> Dict[str, Any]:
    """Record that the user accepted a newer upstream revision.

    Deliberately re-fetches and re-hashes rather than trusting the numbers from
    the check: those may be minutes old, and the pin recorded here is what every
    later verification compares against. It must describe bytes we just saw.
    """
    cid = manifest.get("id", "")
    art = next((a for a in _artifacts(manifest) if a["id"] == artifact_id), None)
    if not art:
        return {"ok": False, "error": "Unknown item"}

    shipped = art["source"]
    repo, branch = shipped.get("repo"), (shipped.get("branch") or "main")
    latest = _latest_ref(repo, branch) if repo else None
    ref = (latest or {}).get("ref") or branch
    url = _raw_url(shipped, ref)
    body = _fetch(url) if url else None
    if body is None:
        return {"ok": False, "error": "Could not download the new version."}

    import hashlib
    digest = hashlib.sha256(body).hexdigest()

    pins = _load_pins()
    pins.setdefault(cid, {})[artifact_id] = {
        "ref": ref,
        "ref_date": (latest or {}).get("date"),
        "sha256": digest,
        "url": url,
        "review_url": f"https://github.com/{repo}/blob/{ref}/{shipped.get('path')}" if repo else None,
        "accepted_at": datetime.utcnow().isoformat(),
    }
    _save_pins(pins)
    logger.info(f"[companions] {cid}/{artifact_id} pinned to {ref[:7]} by the user")

    # An extra is a file we placed — updating the pin means replacing it now.
    # The installer is different: a new one only takes effect when it is re-run,
    # which is the user's call, so we say so rather than running it for them.
    if art.get("extra_id"):
        result = install_extra(manifest, art["extra_id"])
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"),
                    "pinned": ref[:7], "note": "The new version was pinned but not installed."}
        return {"ok": True, "ref": ref[:7], "reinstalled": True}

    return {"ok": True, "ref": ref[:7], "reinstalled": False,
            "note": "Re-run the installer to apply it."}


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
            from utils.subprocess_env import clean_child_env
            proc = subprocess.run(["defaults", "read", domain, key],
                                  capture_output=True, text=True, timeout=10,
                                  env=clean_child_env())
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
            "host_running": host_ok and _host_running(e),
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
            proc = _run_as_user([found, "install", "--cask", cask], timeout=600)
            if not _cask_installed(cask):
                return {"ok": False, "error": _brew_error(proc, cask)}
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

    # Installing SwiftBar puts an app in /Applications; it does not run it. A
    # plugin sitting in a folder no running process is watching produces exactly
    # nothing in the menu bar, which reads as "the toggle did not work".
    # Tell the host where to look BEFORE starting it. SwiftBar asks the user to
    # choose a plugin folder on first run; if they pick a different one, the
    # plugin we just placed is never loaded and the toggle appears to have done
    # nothing. Only set when absent — a folder the user already chose is theirs.
    _point_host_at_plugins(extra, target.parent)
    launched = _launch_host(extra)
    return {"ok": True, "target": str(target),
            "host_installed": _cask_installed(cask),
            "host_launched": launched}


def _point_host_at_plugins(extra: Dict[str, Any], folder: Path) -> bool:
    """Set the host's plugin-folder preference, if it has none yet."""
    pref = (extra.get("install") or {}).get("dir_pref") or {}
    domain, key = pref.get("domain"), pref.get("key")
    if not domain or not key:
        return False
    try:
        existing = _run_as_user(["/usr/bin/defaults", "read", domain, key], timeout=10)
        if existing.returncode == 0 and (existing.stdout or "").strip():
            return False                      # already chosen — leave it alone
        _run_as_user(["/usr/bin/defaults", "write", domain, key, str(folder)], timeout=10)
        logger.info(f"[companions] pointed {domain} at {folder}")
        return True
    except Exception as e:
        logger.debug(f"[companions] could not set {domain}.{key}: {e}")
        return False


def _launch_host(extra: Dict[str, Any]) -> bool:
    """Start the add-on's host app so its icon actually appears."""
    cask = extra.get("requires_cask", "")
    if not cask:
        return False
    app = {"swiftbar": "SwiftBar"}.get(cask, cask)
    try:
        proc = _run_as_user(["/usr/bin/open", "-a", app], timeout=30)
        if proc.returncode == 0:
            logger.info(f"[companions] launched {app} for the menu-bar add-on")
            return True
        logger.warning(f"[companions] could not launch {app}: "
                       f"{(proc.stderr or '').strip()[:160]}")
    except Exception as e:
        logger.warning(f"[companions] could not launch {app}: {e}")
    return False


def _host_running(extra: Dict[str, Any]) -> bool:
    cask = extra.get("requires_cask", "")
    if not cask:
        return True
    app = {"swiftbar": "SwiftBar"}.get(cask, cask)
    try:
        return subprocess.run(["/usr/bin/pgrep", "-x", app],
                              capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


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
        proc = _run_as_user([resolved], timeout=30)
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
        "linked_is_smart": bool(linked_notebook and linked_notebook.get("is_smart")),
        "routing_default": ((manifest.get("produces") or {}).get("routing")
                            or {}).get("default", "notebook"),
        "config": read_config(manifest),
        "using_model": read_config(manifest).get("LLM") if connected else None,
        "install": install_source(manifest),
        "can_control": bool(manifest.get("control", {}).get("start")),
        "has_checks": bool(manifest.get("verify")),
        "extras": extras_status(manifest),
        "audio": _audio_summary(manifest),
        "preflight": preflight_plan(manifest),
        "updates": cached_updates(manifest["id"]),
    }


def _audio_summary(manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the Multi-Output Device is actually made of.

    "We built it for you" is a claim the user has no way to check without
    opening Audio MIDI Setup — which is the detour this whole feature exists to
    remove. Showing its members turns it back into something verifiable.
    """
    mo = ((manifest.get("preflight") or {}).get("audio_setup") or {}).get("multi_output")
    if not mo:
        return None
    try:
        from services.audio_devices import describe_multi_output
        described = describe_multi_output(mo["name"])
    except Exception as e:
        logger.debug(f"[companions] audio summary unavailable: {e}")
        return None
    if not described:
        return {"name": mo["name"], "exists": False, "members": [],
                "ok": False, "why": "not created yet"}

    names = described.get("member_names") or []
    wanted = [w.strip().lower() for w in (mo.get("include") or [])]
    # Correct means BOTH halves are present: something you can hear, and the
    # loopback that captures the far side. One without the other silently
    # produces a recording with no other party, or a call you cannot hear.
    has_capture = any(any(w in n.lower() for w in wanted) for n in names)
    has_audible = any(not any(w in n.lower() for w in wanted) for n in names)
    return {
        "name": described["name"], "exists": True, "members": names,
        "ok": bool(has_capture and has_audible),
        "why": ("plays to your speakers and to the recorder at the same time"
                if has_capture and has_audible else
                "missing the recorder loopback" if not has_capture else
                "nothing audible in it — you would not hear the call"),
    }


def all_status() -> List[Dict[str, Any]]:
    return [status(m) for m in load_manifests()]
