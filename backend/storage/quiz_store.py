"""Quiz generation storage.

Tier 5 (2026-06-02): persists generated quizzes per notebook so they
appear in the Library archive view. Mirrors the audio_store / video_store
pattern — single source of truth for quiz generations.

Each row holds the questions as JSON; the renderer reconstructs the
QuizQuestion shape when reading.
"""
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict

from config import settings


def local_iso_time() -> str:
    return datetime.now().astimezone().isoformat()


class QuizStore:
    def __init__(self):
        self._use_sqlite = settings.use_sqlite
        # Quizzes always store via SQLite — no JSON fallback. The data
        # shape is structured (questions array) and benefits from queries.

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    async def list(self, notebook_id: str) -> List[Dict]:
        """List quizzes for a notebook, newest first."""
        rows = self._get_db().execute(
            "SELECT * FROM quiz_generations WHERE notebook_id = ? ORDER BY created_at DESC",
            (notebook_id,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def get(self, quiz_id: str) -> Optional[Dict]:
        row = self._get_db().execute(
            "SELECT * FROM quiz_generations WHERE quiz_id = ?",
            (quiz_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    async def create(
        self,
        notebook_id: str,
        topic: str = "",
        difficulty: str = "medium",
        num_questions: int = 5,
        questions: Optional[List[Dict]] = None,
        source_summary: str = "",
        sources_used: int = 0,
    ) -> Dict:
        quiz_id = str(uuid.uuid4())
        now = local_iso_time()
        questions_json = json.dumps(questions or [])

        conn = self._get_db()
        conn.execute(
            """INSERT INTO quiz_generations
               (quiz_id, notebook_id, topic, difficulty, num_questions,
                questions_json, source_summary, sources_used,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (quiz_id, notebook_id, topic, difficulty, num_questions,
             questions_json, source_summary, sources_used, now, now),
        )
        conn.commit()
        return await self.get(quiz_id) or {}

    async def delete(self, quiz_id: str) -> bool:
        conn = self._get_db()
        cur = conn.execute("DELETE FROM quiz_generations WHERE quiz_id = ?", (quiz_id,))
        conn.commit()
        return cur.rowcount > 0

    def _row_to_dict(self, row) -> Dict:
        d = dict(row)
        if d.get("questions_json"):
            try:
                d["questions"] = json.loads(d["questions_json"])
            except (json.JSONDecodeError, TypeError):
                d["questions"] = []
        else:
            d["questions"] = []
        d.pop("questions_json", None)
        return d


# Module-level singleton, mirrors audio_store / video_store convention.
quiz_store = QuizStore()
