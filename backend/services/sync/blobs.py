"""Audio and video files between Macs (LB-12 phase E, D15: eager).

Rows travel as records with `@data/<relative>` paths (12g); the files they point
at travel here, in signed 4 MB chunks over the same mutual-TLS link:

  * resumable — a transfer writes `<file>.part` and continues from its size;
  * verified  — the whole file's SHA-256 must match before it is moved into place;
  * confined  — only paths under `audio/` and `video/` inside the data dir, never
    `..`, never absolute: a peer cannot read or write anything else.

The initiating Mac both downloads what it is missing and uploads what the peer is
missing, so the outbound-only work Mac (D8) converges both ways. Transfers skip a
round while the user is active (presence) — sync is never in their way.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

BLOB_DIRS = ("audio", "video")
CHUNK = 4 * 1024 * 1024
_SOURCES = (("audio_generations", "audio_file_path"), ("video_generations", "video_file_path"))


class BlobPathError(ValueError):
    pass


def data_dir() -> Path:
    from config import settings

    return Path(settings.data_dir).resolve()


def safe_path(rel: str) -> Path:
    """`rel` (relative to the data dir) → an absolute path, or refuse."""
    rel = (rel or "").replace("\\", "/")
    if rel.startswith("@data/"):
        rel = rel[len("@data/"):]
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise BlobPathError(f"refusing path {rel!r}")
    if rel.split("/", 1)[0] not in BLOB_DIRS:
        raise BlobPathError(f"{rel!r} is not an audio or video file")
    base = data_dir()
    p = (base / rel).resolve()
    if base not in p.parents:
        raise BlobPathError(f"refusing path {rel!r}")
    return p


def _rel(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    base = str(data_dir()).rstrip("/") + "/"
    v = str(value)
    if v.startswith("@data/"):
        v = v[len("@data/"):]
    elif v.startswith(base):
        v = v[len(base):]
    elif v.startswith("/"):
        return None                        # outside the data dir: not ours to sync
    try:
        safe_path(v)
    except BlobPathError:
        return None
    return v


def referenced(conn) -> List[str]:
    """Every audio/video file a synced row points at (relative paths)."""
    out = set()
    for table, col in _SOURCES:
        try:
            for (v,) in conn.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL'):
                r = _rel(v)
                if r:
                    out.add(r)
        except Exception:
            continue
    return sorted(out)


def missing(rels: List[str]) -> List[str]:
    out = []
    for r in rels:
        try:
            if not safe_path(r).exists():
                out.append(r)
        except BlobPathError:
            continue
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_chunk(rel: str, offset: int) -> Dict[str, Any]:
    p = safe_path(rel)
    if not p.exists():
        raise FileNotFoundError(rel)
    total = p.stat().st_size
    with open(p, "rb") as f:
        f.seek(max(0, int(offset)))
        data = f.read(CHUNK)
    return {"path": rel, "offset": int(offset), "total": total,
            "sha256": sha256(p) if int(offset) == 0 else None,
            "data": base64.b64encode(data).decode("ascii")}


def write_chunk(rel: str, offset: int, data_b64: str, total: int, digest: str) -> Dict[str, Any]:
    """Append one chunk to `<file>.part`; on the last one verify and move into place."""
    p = safe_path(rel)
    part = p.with_name(p.name + ".part")
    p.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if int(offset) != have:
        return {"next": have, "done": False}            # resume from what is really here
    with open(part, "ab") as f:
        f.write(base64.b64decode(data_b64))
    size = part.stat().st_size
    if size < int(total):
        return {"next": size, "done": False}
    if sha256(part) != digest:
        part.unlink(missing_ok=True)
        raise ValueError(f"{rel}: hash mismatch — discarded, will retry")
    os.replace(part, p)
    return {"next": size, "done": True}


def partial_offset(rel: str) -> int:
    p = safe_path(rel)
    part = p.with_name(p.name + ".part")
    return part.stat().st_size if part.exists() else 0
