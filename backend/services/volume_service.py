"""The encrypted volume the data directory lives on.

LB-11 of the v2.5.0 plan. An APFS-encrypted sparsebundle (AES-256) at
`~/Library/Application Support/LocalBook.sparsebundle`, mounted at the existing
data path so nothing above this layer needs to know it moved.

**The mechanism was proven before any of this was written** (K-1 gate items 1–3
and 8, on the mini, 2026-09-29), and the two corrections that came out of it are
load-bearing:

    create:  diskutil image create blank --size N --fs APFS --encrypt \\
             --stdinpassphrase --volumeName <name> <path>
    attach:  security find-generic-password -w | hdiutil attach -stdinpass \\
             -nobrowse -mountpoint <dir> <image>

  * **NOT `hdiutil create -encryption`** — deprecated on macOS 27, which prints a
    warning and tells you to use `diskutil image create`.
  * **`hdiutil attach` does NOT resolve a keychain password by itself.** With no
    password source it blocks waiting to prompt, so it is unusable headless. We
    read the password and pipe it, which is also entirely under our control.

**On the plan's contradiction.** LB-11 says the volume key "comes from
`keyvault("volume")`"; D20 and K-1 say the volume password is "held by macOS as a
standard disk-image password in the login keychain with a permissive ACL". Those
are the same thing here: `keyvault` stores via `security add-generic-password -A`,
which IS D20's mechanism, so nothing about the app's code signature is consulted
and an ad-hoc rebuild reads the same item. K-1(c) additionally requires a wrapped
copy of the volume password for phrase recovery, which going through `keyvault`
gives for free.

**The sentinel is the whole of fail-closed.** If the image fails to attach, the
mount point is an ordinary empty directory — and an empty data directory is
indistinguishable from a new install. LocalBook would come up cheerfully blank
and, worse, start writing a fresh corpus over the top. So `.volume_id` lives
INSIDE the image, and "is the volume mounted?" means "is the sentinel there?",
never "does the path exist?".

**Detach is the dangerous direction.** Four SQLite databases with live WALs sit
on that volume. `hdiutil detach -force` while they are open is how a WAL is
truncated mid-checkpoint. So: stop the backend, confirm it is gone, detach
politely with retries, and force only after all of that — and say so in the log
when it comes to that.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Suffix only — the stem comes from the mount point, see image_path().
IMAGE_SUFFIX = ".sparsebundle"
VOLUME_NAME = "LocalBook"
SENTINEL = ".volume_id"

# A sparsebundle's `--size` is a CEILING, not an allocation: the bands on disk
# only ever total what the data actually needs. Generous so it never has to be
# resized, which is an operation with its own failure modes.
DEFAULT_MAX_SIZE_GB = 512

# Every `diskutil`/`hdiutil` call is local and quick. A block means something is
# prompting or wedged, and failing fast beats hanging a launch forever.
_TIMEOUT = 120
_ATTACH_TIMEOUT = 60

# Detach politely this many times before even considering force.
_DETACH_ATTEMPTS = 5
_DETACH_BACKOFF = 1.5

# create() probes encryption while the image is still detached; attach() stamps
# that verdict into the sentinel. Kept here rather than passed through because
# the two calls are separate public operations.
_verified_encrypted: Dict[str, bool] = {}


class VolumeError(RuntimeError):
    """Something about the encrypted volume could not be done.

    Always raised rather than returning a falsy result: every caller of this
    module is deciding whether the user's data is reachable, and a silent "no"
    there becomes an app that starts blank.
    """


@dataclass
class VolumeState:
    image_path: Path
    mount_point: Path
    exists: bool
    mounted: bool
    encrypted: Optional[bool] = None
    encrypted_source: Optional[str] = None
    sentinel_ok: bool = False
    volume_id: Optional[str] = None
    image_bytes: int = 0
    free_bytes: int = 0
    detail: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "image_path": str(self.image_path),
            "mount_point": str(self.mount_point),
            "exists": self.exists,
            "mounted": self.mounted,
            "encrypted": self.encrypted,
            "encrypted_source": self.encrypted_source,
            "sentinel_ok": self.sentinel_ok,
            "volume_id": self.volume_id,
            "image_bytes": self.image_bytes,
            "free_bytes": self.free_bytes,
            "detail": self.detail,
        }


# ── paths ───────────────────────────────────────────────────────────────────


def mount_point() -> Path:
    """Where the volume mounts: the existing data dir, unchanged.

    Nothing above this layer learns that the data moved — `settings.data_dir`
    keeps meaning what it always meant. That is the single-path-resolution
    requirement (LB-11 measure 6) satisfied by not introducing a second path in
    the first place.
    """
    from config import settings

    return Path(settings.data_dir)


def image_path() -> Path:
    """The sparsebundle, BESIDE the mount point and NAMED AFTER it.

    Inside would be nonsense — the image cannot contain its own mount point —
    and this is also where the wrapped keys already live, so the whole recovery
    story sits in one directory that is never itself encrypted.

    Named after the mount point, not fixed: a fixed `LocalBook.sparsebundle`
    would have the dev sandbox and production sharing ONE encrypted volume,
    each mounting it at a different path and each believing it owned the
    contents. Exactly the mistake the keys directory had an hour earlier.

        ~/Library/Application Support/LocalBook       →  LocalBook.sparsebundle
        ~/Library/Application Support/LocalBook-dev   →  LocalBook-dev.sparsebundle
    """
    mp = mount_point()
    return mp.parent / f"{mp.name}.sparsebundle"


# ── the password ────────────────────────────────────────────────────────────


def passphrase() -> str:
    """The volume password, as the text `hdiutil` wants.

    32 random bytes from `keyvault`, rendered base64 — 44 characters of real
    entropy. Going through `keyvault` means it is stored with a permissive ACL
    (D20: an ad-hoc rebuild must read the same item) AND gets a wrapped copy for
    phrase recovery (K-1 c) without any extra machinery.
    """
    from services import keyvault

    return base64.b64encode(keyvault.get_or_create("volume")).decode("ascii")


def passphrase_exists() -> bool:
    from services import keyvault

    try:
        return keyvault._keychain_read("volume") is not None
    except Exception:
        return False


# ── shelling out ────────────────────────────────────────────────────────────


def _run(args: List[str], *, stdin: Optional[str] = None,
         timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise VolumeError(
            f"`{args[0]} {args[1] if len(args) > 1 else ''}` timed out after "
            f"{timeout}s — it is most likely waiting on a prompt."
        ) from exc
    except FileNotFoundError as exc:
        raise VolumeError(f"{args[0]} not found; this module is macOS-only") from exc


# ── state ───────────────────────────────────────────────────────────────────


def _is_encrypted(image: Path) -> Optional[bool]:
    """Probe the image. Only works while it is DETACHED.

    ⚠️ `hdiutil isencrypted` on a MOUNTED image fails with "Resource temporarily
    unavailable" — verified 2026-09-29. Since the volume is mounted almost all
    the time in production, probing alone would report encryption as *unknown*
    exactly when everything is working. `state()` falls back to the verdict
    recorded in the sentinel, which was probed at creation while detached.
    """
    proc = _run(["hdiutil", "isencrypted", str(image)])
    if proc.returncode != 0:
        return None
    match = re.search(r"encrypted:\s*(YES|NO)", proc.stdout)
    if match:
        return match.group(1) == "YES"
    # A mounted image prints "isencrypted failed"; absence of the field is not
    # evidence of absence of encryption.
    return None


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def read_sentinel(mp: Optional[Path] = None) -> Optional[Dict[str, object]]:
    """The sentinel from inside the volume, or None.

    None means the volume is NOT mounted, whatever `mount` says and whatever the
    directory looks like. That distinction is the point: a failed attach leaves
    an ordinary empty directory at the mount point, and treating that as "no data
    yet" is how an encrypted install comes up blank and starts writing over
    itself.
    """
    path = (mp or mount_point()) / SENTINEL
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning("[volume] sentinel at %s is unreadable: %s", path, exc)
        return None


def _write_sentinel(mp: Path, *, encrypted: Optional[bool] = None) -> Dict[str, object]:
    payload = {
        "volume_id": os.urandom(8).hex(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "volume_name": VOLUME_NAME,
        # Recorded because it can only be PROBED while detached, and the volume
        # is mounted almost always. `create()` verifies it on the detached image
        # and refuses if it comes back anything but True, so this is a record of
        # a real check rather than an assertion.
        "encrypted": encrypted,
    }
    (mp / SENTINEL).write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def is_mounted(mp: Optional[Path] = None) -> bool:
    """Mounted AND ours. Never just "the path exists"."""
    return read_sentinel(mp) is not None


def state() -> VolumeState:
    image = image_path()
    mp = mount_point()
    sentinel = read_sentinel(mp)

    free = 0
    if sentinel is not None:
        try:
            stat = os.statvfs(mp)
            free = stat.f_bavail * stat.f_frsize
        except OSError:
            pass

    # Probe when we can (detached), fall back to the recorded verdict when we
    # cannot (mounted). Reporting "unknown" while the volume is happily serving
    # encrypted data would be the least useful true statement available.
    if not image.exists():
        encrypted = None
        source = None
    elif sentinel is None:
        encrypted = _is_encrypted(image)
        source = "probed"
    else:
        encrypted = sentinel.get("encrypted")
        source = "recorded at creation"
        if encrypted is None:
            encrypted = _is_encrypted(image)
            source = "probed"

    return VolumeState(
        image_path=image,
        mount_point=mp,
        exists=image.exists(),
        mounted=sentinel is not None,
        encrypted=encrypted,
        encrypted_source=source,
        sentinel_ok=sentinel is not None,
        volume_id=(sentinel or {}).get("volume_id"),
        image_bytes=_dir_bytes(image) if image.exists() else 0,
        free_bytes=free,
    )


# ── create ──────────────────────────────────────────────────────────────────


def create(*, max_size_gb: Optional[int] = None) -> VolumeState:
    """Make the encrypted image. Refuses if one is already there.

    Never touches the mount point's current contents — creating the volume and
    moving data into it are separate operations on purpose, so a failure here
    cannot cost anything.
    """
    image = image_path()
    if image.exists():
        raise VolumeError(f"{image} already exists")

    size = int(max_size_gb or _configured_max_size_gb())
    image.parent.mkdir(parents=True, exist_ok=True)

    # `diskutil image create`, NOT `hdiutil create -encryption` — the latter is
    # deprecated on macOS 27 and prints a warning telling you so.
    proc = _run(
        [
            "diskutil", "image", "create", "blank",
            "--size", f"{size}g",
            "--fs", "APFS",
            "--encrypt",
            "--stdinpassphrase",
            "--volumeName", VOLUME_NAME,
            # The FULL path, suffix included. `diskutil` picks the image format
            # from the extension: pass `.sparsebundle` and you get one; strip it
            # and you silently get a fixed-size `.dmg` instead. That mounts
            # perfectly, allocates the whole ceiling immediately, and makes
            # `hdiutil compact` a no-op — a 512 GB file where a 600 MB bundle
            # was intended. Verified both ways, 2026-09-29.
            str(image),
        ],
        stdin=passphrase(),
    )
    if proc.returncode != 0:
        raise VolumeError(
            f"could not create the encrypted image: "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
        )

    if not image.exists():
        raise VolumeError(
            f"diskutil reported success but {image.name} is not there"
        )
    if not image.is_dir():
        # A sparsebundle is a DIRECTORY of bands. A file here means the format
        # went wrong, which is the .dmg case above.
        raise VolumeError(
            f"{image.name} is a file, not a sparsebundle — refusing to use it"
        )

    if _is_encrypted(image) is not True:
        raise VolumeError(
            f"{image} was created but does not report itself encrypted — "
            f"refusing to use it"
        )
    _verified_encrypted[str(image)] = True

    logger.info("[volume] created %s (%d GB ceiling, AES-256)", image.name, size)
    return state()


def _configured_max_size_gb() -> int:
    try:
        from config import settings

        return max(8, int(getattr(settings, "volume_max_size_gb", DEFAULT_MAX_SIZE_GB)))
    except Exception:
        return DEFAULT_MAX_SIZE_GB


# ── attach ──────────────────────────────────────────────────────────────────


def attach(*, initialise_sentinel: bool = False) -> VolumeState:
    """Mount the volume at the data dir. Idempotent.

    Idempotence is not a nicety: the watchdog restarts the sidecar, and LB-11
    requires this to be safe across five restarts in a row. Already mounted is a
    success, not an error.
    """
    image = image_path()
    mp = mount_point()

    if is_mounted(mp):
        return state()

    if not image.exists():
        raise VolumeError(
            f"the encrypted volume is missing: {image} does not exist. "
            f"Your data is not lost — recover it from the phrase or a backup."
        )
    if not passphrase_exists():
        raise VolumeError(
            "this Mac has no volume password in its Keychain. Recover it with "
            "your 24-word phrase."
        )

    mp.mkdir(parents=True, exist_ok=True)

    # The mount point must be EMPTY. Mounting over real files hides them, and a
    # user who then adds sources is writing into the volume while their old data
    # sits invisible underneath.
    stray = [p.name for p in mp.iterdir() if p.name != SENTINEL]
    if stray:
        raise VolumeError(
            f"{mp} is not empty ({len(stray)} item(s), e.g. {stray[:3]}). Mounting "
            f"over them would hide them. Migrate them in first."
        )

    proc = _run(
        [
            "hdiutil", "attach",
            "-stdinpass",
            "-nobrowse",          # keep it out of Finder's sidebar
            "-mountpoint", str(mp),
            str(image),
        ],
        stdin=passphrase(),
        timeout=_ATTACH_TIMEOUT,
    )
    if proc.returncode != 0:
        raise VolumeError(
            f"could not unlock the volume: "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
        )

    if initialise_sentinel and read_sentinel(mp) is None:
        # `verified_encrypted` was probed by create() on the DETACHED image; it
        # cannot be re-probed now that we have mounted it.
        _write_sentinel(mp, encrypted=_verified_encrypted.pop(str(image), None))

    if read_sentinel(mp) is None:
        # Attached, but not OUR volume — or an image that was never initialised.
        # Refuse rather than proceed: this is the case that otherwise becomes an
        # app writing a fresh corpus into a stranger's volume.
        #
        # ⚠️ Detach FIRST. An earlier version raised here and left the volume
        # mounted over the data dir, where nothing was watching it — and because
        # `is_mounted()` is sentinel-based, neither the app nor the test cleanup
        # could see it to unmount it. Four leaked mounts in one test run.
        try:
            _run(["hdiutil", "detach", str(mp)])
        except VolumeError:
            pass
        raise VolumeError(
            f"{mp} mounted but carries no {SENTINEL} — this is not LocalBook's "
            f"volume, or it was never initialised. It has been unmounted again "
            f"and nothing was written."
        )

    _disable_spotlight(mp)
    logger.info("[volume] mounted at %s", mp)
    return state()


def _disable_spotlight(mp: Path) -> None:
    """LB-11 measure 9. Best-effort: it saves CPU, and the index would sit
    inside the image anyway, so failing is cosmetic."""
    try:
        proc = _run(["mdutil", "-i", "off", str(mp)], timeout=30)
        if proc.returncode == 0:
            logger.info("[volume] Spotlight indexing disabled on %s", mp)
        else:
            logger.debug("[volume] could not disable Spotlight: %s", proc.stderr)
    except VolumeError as exc:
        logger.debug("[volume] Spotlight: %s", exc)


# ── detach ──────────────────────────────────────────────────────────────────


def detach(*, allow_force: bool = False) -> Dict[str, object]:
    """Unmount the volume, politely.

    ⚠️ Four SQLite databases with live WALs are on this volume. Forcing a detach
    while they are open truncates a WAL mid-checkpoint, which is corruption with
    extra steps. The caller is expected to have stopped the backend FIRST; this
    retries with backoff and forces only when explicitly allowed, and says so
    loudly when it comes to that.
    """
    mp = mount_point()
    if not is_mounted(mp):
        return {"detached": True, "already": True}

    last = ""
    for attempt in range(1, _DETACH_ATTEMPTS + 1):
        proc = _run(["hdiutil", "detach", str(mp)])
        if proc.returncode == 0:
            logger.info("[volume] detached on attempt %d", attempt)
            return {"detached": True, "attempts": attempt, "forced": False}
        last = (proc.stderr or proc.stdout or "").strip()
        logger.warning("[volume] detach attempt %d failed: %s", attempt, last[:200])
        time.sleep(_DETACH_BACKOFF * attempt)

    if not allow_force:
        raise VolumeError(
            f"could not detach {mp} after {_DETACH_ATTEMPTS} attempts — something "
            f"still has it open. Refusing to force: that truncates a live WAL. "
            f"Last error: {last[:200]}"
        )

    logger.error(
        "[volume] FORCING detach of %s after %d polite attempts — if the backend "
        "was still writing, check integrity on the next launch",
        mp, _DETACH_ATTEMPTS,
    )
    proc = _run(["hdiutil", "detach", str(mp), "-force"])
    if proc.returncode != 0:
        raise VolumeError(f"even a forced detach failed: {(proc.stderr or '').strip()[:200]}")
    return {"detached": True, "attempts": _DETACH_ATTEMPTS, "forced": True}


# ── housekeeping ────────────────────────────────────────────────────────────


def compact() -> Dict[str, object]:
    """Reclaim space from deleted bands (LB-11 measure 7, a NIGHT job).

    Requires the volume DETACHED — `hdiutil compact` on a mounted image either
    refuses or does nothing useful. Reports rather than raising when it is
    mounted, because this runs unattended and a nightly job that raises on a
    perfectly normal state is just noise.
    """
    image = image_path()
    if not image.exists():
        return {"compacted": False, "reason": "no image"}
    if is_mounted():
        return {"compacted": False, "reason": "the volume is mounted"}

    before = _dir_bytes(image)
    proc = _run(["hdiutil", "compact", str(image)], stdin=passphrase(), timeout=900)
    if proc.returncode != 0:
        return {"compacted": False, "reason": (proc.stderr or "").strip()[:200]}
    after = _dir_bytes(image)
    logger.info("[volume] compacted %s: %.1f → %.1f MB",
                image.name, before / 1024 ** 2, after / 1024 ** 2)
    return {"compacted": True, "before_bytes": before, "after_bytes": after,
            "reclaimed_bytes": max(0, before - after)}


def initialise_new_volume() -> VolumeState:
    """Create, mount and stamp a brand-new volume. For the setup flow.

    Deliberately separate from the migration: this produces a working EMPTY
    volume, and moving the user's data into it is a different operation with a
    backup in front of it.
    """
    if not image_path().exists():
        create()
    return attach(initialise_sentinel=True)
