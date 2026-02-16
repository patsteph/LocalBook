"""Content generation storage - persists generated documents"""
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json


def local_iso_time() -> str:
    """Get current local time as ISO string"""
    return datetime.now().astimezone().isoformat()


class ContentStore:
    def __init__(self):
        self._use_sqlite = settings.use_sqlite
        self.storage_path = settings.data_dir / "content_generations.json"
        if not self._use_sqlite:
            self._ensure_storage()

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"generations": {}})

    def _load_data(self) -> dict:
        """Load content generations from storage"""
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save content generations to storage"""
        atomic_write_json(self.storage_path, data)

    async def list(self, notebook_id: str) -> List[Dict]:
        """List all content generations for a notebook"""
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT * FROM content_generations WHERE notebook_id = ? ORDER BY created_at DESC",
                (notebook_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        data = self._load_data()
        generations = [
            g for g in data["generations"].values()
            if g.get("notebook_id") == notebook_id
        ]
        generations.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return generations

    async def create(
        self,
        notebook_id: str,
        skill_id: str,
        skill_name: str,
        content: str,
        topic: Optional[str] = None,
        sources_used: int = 0
    ) -> Dict:
        """Create a new content generation record"""
        content_id = str(uuid.uuid4())
        now = local_iso_time()

        generation = {
            "content_id": content_id,
            "notebook_id": notebook_id,
            "skill_id": skill_id,
            "skill_name": skill_name,
            "content": content,
            "topic": topic,
            "sources_used": sources_used,
            "created_at": now,
            "updated_at": now
        }

        if self._use_sqlite:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO content_generations
                   (content_id, notebook_id, skill_id, skill_name, content,
                    topic, sources_used, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (content_id, notebook_id, skill_id, skill_name, content,
                 topic, sources_used, now, now)
            )
            conn.commit()
        else:
            data = self._load_data()
            data["generations"][content_id] = generation
            self._save_data(data)

        return generation

    async def get(self, content_id: str) -> Optional[Dict]:
        """Get a content generation by ID"""
        if self._use_sqlite:
            row = self._get_db().execute(
                "SELECT * FROM content_generations WHERE content_id = ?", (content_id,)
            ).fetchone()
            return dict(row) if row else None
        data = self._load_data()
        return data["generations"].get(content_id)

    async def get_by_notebook(self, notebook_id: str, content_id: str) -> Optional[Dict]:
        """Get a content generation by notebook and content ID"""
        generation = await self.get(content_id)
        if generation and generation.get("notebook_id") == notebook_id:
            return generation
        return None

    async def delete(self, content_id: str) -> bool:
        """Delete a content generation"""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute("DELETE FROM content_generations WHERE content_id = ?", (content_id,))
            conn.commit()
            return cursor.rowcount > 0
        data = self._load_data()
        if content_id in data["generations"]:
            del data["generations"][content_id]
            self._save_data(data)
            return True
        return False


content_store = ContentStore()
