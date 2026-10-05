"""Irreplaceable JSON/YAML as synced records (LB-12 D1).

Core memory, the user profile, app preferences, Curator's and each Collector's
configuration, quiz cards and reviews, people settings and the approval queue
used to be files with ~20 writers and no change journal — they could not sync.
They are rows in `documents(kind, key, uuid, body_json, updated_at)` now, a
synced table, written through this module only.

Keys are GRANULAR where two Macs are likely to change the same thing apart: one
row per core-memory entry, per quiz card, per review, per approval item — so two
memories added on two Macs both survive instead of colliding as one document.

The old files are read ONCE (`import_file`) and left in place as a fallback; they
are never written again.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _conn():
    from storage.database import get_db

    return get_db().get_connection()


def get(kind: str, key: str, default: Any = None) -> Any:
    row = _conn().execute("SELECT body_json FROM documents WHERE kind=? AND key=?", (kind, key)).fetchone()
    return json.loads(row[0]) if row else default


def exists(kind: str, key: str) -> bool:
    return _conn().execute("SELECT 1 FROM documents WHERE kind=? AND key=?", (kind, key)).fetchone() is not None


def put(kind: str, key: str, body: Any) -> None:
    """Write one document — and only if it changed (an unchanged rewrite would
    still journal, and every Mac would see a pointless change)."""
    raw = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    conn = _conn()
    row = conn.execute("SELECT body_json FROM documents WHERE kind=? AND key=?", (kind, key)).fetchone()
    if row and row[0] == raw:
        return
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO documents (kind, key, uuid, body_json, updated_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(kind, key) DO UPDATE SET body_json=excluded.body_json,
                                                updated_at=excluded.updated_at""",
        (kind, key, uuid.uuid4().hex, raw, now))
    conn.commit()


def delete(kind: str, key: str) -> None:
    conn = _conn()
    conn.execute("DELETE FROM documents WHERE kind=? AND key=?", (kind, key))
    conn.commit()


def items(kind: str, prefix: str = "") -> List[Tuple[str, Any]]:
    """(key, body) for every document of `kind` whose key starts with `prefix`."""
    rows = _conn().execute(
        "SELECT key, body_json FROM documents WHERE kind=? AND key LIKE ? ESCAPE '\\' ORDER BY key",
        (kind, prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")).fetchall()
    return [(k, json.loads(b)) for k, b in rows]


def replace_set(kind: str, prefix: str, bodies: Dict[str, Any]) -> None:
    """Make the documents under `prefix` exactly `bodies` ({key: body}): write
    what changed, delete what is gone. Untouched rows are not rewritten."""
    have = {k for k, _ in items(kind, prefix)}
    for key, body in bodies.items():
        put(kind, key, body)
    for key in have - set(bodies):
        delete(kind, key)


def import_file(kind: str, key: str, path: Path, load: Callable[[Path], Any]) -> Optional[Any]:
    """The one-time move from a file: if the document does not exist yet and the
    file does, copy it in. Returns the document (or None)."""
    if exists(kind, key):
        return get(kind, key)
    if not Path(path).exists():
        return None
    try:
        body = load(Path(path))
    except Exception as exc:
        logger.warning("[documents] could not import %s: %s", path, exc)
        return None
    if body is not None:
        put(kind, key, body)
        logger.info("[documents] imported %s into %s/%s", path, kind, key)
    return body


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text())


def read_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(Path(path).read_text()) or {}
