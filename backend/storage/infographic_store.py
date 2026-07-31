"""Infographic generation storage.

2.2.0 (2026-07-31): persists generated infographics (the `json:infographic`
Artifact payload) per notebook so they appear in the Library. Before this,
infographics only lived as in-memory canvas items and were lost on app
restart — the one Studio output lane with a persistence gap (the generic
`visual` templates are already covered by `visual_store`).

Mirrors `visual_store.py`. The difference: an infographic's payload is a JSON
dict (L1 recharts config / L2 body-html / L3 scene_svg, plus sources, style,
lane, archetype, degraded) rather than a single SVG/Mermaid string, so the
WHOLE `artifact.payload` is stored as `payload_json`.

Self-contained schema: the `infographic_generations` table is created lazily
(CREATE TABLE IF NOT EXISTS) on first connection, keyed off `settings.data_dir`
read fresh each call — so tests can monkeypatch `settings.data_dir` to a tmp
dir and never touch production data. Every method never raises.
"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import settings


def local_iso_time() -> str:
    return datetime.now().astimezone().isoformat()


class InfographicStore:
    def __init__(self):
        # Thread-local connection, re-resolved whenever settings.data_dir
        # changes (production is stable → cached; tests point it at a tmp dir).
        self._local = threading.local()

    def _get_db(self) -> sqlite3.Connection:
        path = str(settings.data_dir / "localbook.db")
        conn = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "path", None) != path:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._ensure_schema(conn)
            self._local.conn = conn
            self._local.path = path
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS infographic_generations (
                infographic_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                topic TEXT,
                title TEXT,
                lane TEXT,
                archetype TEXT,
                payload_json TEXT,
                degraded INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_infographic_notebook "
            "ON infographic_generations(notebook_id)"
        )
        conn.commit()

    async def list(self, notebook_id: str) -> List[Dict]:
        try:
            rows = self._get_db().execute(
                "SELECT * FROM infographic_generations WHERE notebook_id = ? "
                "ORDER BY created_at DESC",
                (notebook_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    async def get(self, infographic_id: str) -> Optional[Dict]:
        try:
            row = self._get_db().execute(
                "SELECT * FROM infographic_generations WHERE infographic_id = ?",
                (infographic_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    async def create(
        self,
        notebook_id: str,
        topic: str = "",
        title: str = "",
        lane: Optional[str] = None,
        archetype: Optional[str] = None,
        payload: Optional[Any] = None,
        degraded: bool = False,
    ) -> Dict:
        try:
            if not notebook_id:
                return {}
            infographic_id = str(uuid.uuid4())
            now = local_iso_time()
            # payload comes from artifact.model_dump() → already JSON-safe;
            # default=str is a belt-and-braces guard against stray objects.
            payload_json = json.dumps(payload if payload is not None else {}, default=str)
            conn = self._get_db()
            conn.execute(
                """INSERT INTO infographic_generations
                   (infographic_id, notebook_id, topic, title, lane, archetype,
                    payload_json, degraded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    infographic_id, notebook_id, topic or "", title or "",
                    lane, archetype, payload_json, 1 if degraded else 0, now, now,
                ),
            )
            conn.commit()
            return await self.get(infographic_id) or {}
        except Exception:
            return {}

    async def delete(self, infographic_id: str) -> bool:
        try:
            conn = self._get_db()
            cur = conn.execute(
                "DELETE FROM infographic_generations WHERE infographic_id = ?",
                (infographic_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            return False


infographic_store = InfographicStore()
