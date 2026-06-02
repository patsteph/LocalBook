"""Visual generation storage.

Tier 5 (2026-06-02): persists generated visuals (Mermaid + SVG) per
notebook so they appear in the Library. Before this, visuals only lived
as canvas items and were lost on app restart.

Stores either svg_markup (for v2 composer / Klein full-bleed / skeletons)
or mermaid_code (for the legacy template path). The renderer picks the
right field at display time.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Dict

from config import settings


def local_iso_time() -> str:
    return datetime.now().astimezone().isoformat()


class VisualStore:
    def __init__(self):
        self._use_sqlite = settings.use_sqlite

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    async def list(self, notebook_id: str) -> List[Dict]:
        rows = self._get_db().execute(
            "SELECT * FROM visual_generations WHERE notebook_id = ? ORDER BY created_at DESC",
            (notebook_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    async def get(self, visual_id: str) -> Optional[Dict]:
        row = self._get_db().execute(
            "SELECT * FROM visual_generations WHERE visual_id = ?",
            (visual_id,)
        ).fetchone()
        return dict(row) if row else None

    async def create(
        self,
        notebook_id: str,
        topic: str = "",
        title: str = "",
        svg_markup: Optional[str] = None,
        mermaid_code: Optional[str] = None,
        template_id: Optional[str] = None,
        v2_path: Optional[str] = None,
        critic_overall: Optional[float] = None,
        prompt: Optional[str] = None,
    ) -> Dict:
        visual_id = str(uuid.uuid4())
        now = local_iso_time()

        conn = self._get_db()
        conn.execute(
            """INSERT INTO visual_generations
               (visual_id, notebook_id, topic, title, svg_markup, mermaid_code,
                template_id, v2_path, critic_overall, prompt,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (visual_id, notebook_id, topic, title, svg_markup, mermaid_code,
             template_id, v2_path, critic_overall, prompt, now, now),
        )
        conn.commit()
        return await self.get(visual_id) or {}

    async def delete(self, visual_id: str) -> bool:
        conn = self._get_db()
        cur = conn.execute("DELETE FROM visual_generations WHERE visual_id = ?", (visual_id,))
        conn.commit()
        return cur.rowcount > 0


visual_store = VisualStore()
