"""Video generation storage"""
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json


def local_iso_time() -> str:
    """Get current local time as ISO string"""
    return datetime.now().astimezone().isoformat()


class VideoStore:
    def __init__(self):
        self._use_sqlite = settings.use_sqlite
        self.storage_path = settings.data_dir / "video_generations.json"
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
        """Load video generations from storage"""
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save video generations to storage"""
        atomic_write_json(self.storage_path, data)

    async def list(self, notebook_id: str) -> List[Dict]:
        """List all video generations for a notebook"""
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT * FROM video_generations WHERE notebook_id = ? ORDER BY created_at DESC",
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
        topic: str = "",
        duration_minutes: int = 5,
        visual_style: str = "classic",
        voice: str = "us_female",
        format_type: str = "explainer",
    ) -> Dict:
        """Create a new video generation record"""
        video_id = str(uuid.uuid4())
        now = local_iso_time()

        generation = {
            "video_id": video_id,
            "notebook_id": notebook_id,
            "topic": topic,
            "duration_minutes": duration_minutes,
            "visual_style": visual_style,
            "voice": voice,
            "format_type": format_type,
            "video_file_path": None,
            "duration_seconds": None,
            "storyboard": None,
            "narration_script": None,
            "slide_count": None,
            "status": "pending",
            "error_message": None,
            "created_at": now,
            "updated_at": now
        }

        if self._use_sqlite:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO video_generations
                   (video_id, notebook_id, topic, duration_minutes, visual_style,
                    voice, format_type, video_file_path, duration_seconds,
                    storyboard, narration_script, slide_count,
                    status, error_message, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video_id, notebook_id, topic, duration_minutes, visual_style,
                 voice, format_type, None, None,
                 None, None, None,
                 'pending', None, now, now)
            )
            conn.commit()
        else:
            data = self._load_data()
            data["generations"][video_id] = generation
            self._save_data(data)

        return generation

    async def get(self, video_id: str) -> Optional[Dict]:
        """Get a video generation by ID"""
        if self._use_sqlite:
            row = self._get_db().execute(
                "SELECT * FROM video_generations WHERE video_id = ?", (video_id,)
            ).fetchone()
            return dict(row) if row else None
        data = self._load_data()
        return data["generations"].get(video_id)

    async def update(self, video_id: str, updates: Dict) -> Optional[Dict]:
        """Update a video generation"""
        if self._use_sqlite:
            now = local_iso_time()
            allowed = {'topic', 'video_file_path', 'duration_seconds',
                       'storyboard', 'narration_script', 'slide_count',
                       'status', 'error_message', 'visual_style', 'voice',
                       'format_type', 'duration_minutes'}
            sets = []
            params = []
            for k, v in updates.items():
                if k in allowed:
                    sets.append(f"{k} = ?")
                    params.append(v)
            if not sets:
                return await self.get(video_id)
            sets.append("updated_at = ?")
            params.append(now)
            params.append(video_id)
            conn = self._get_db()
            conn.execute(f"UPDATE video_generations SET {', '.join(sets)} WHERE video_id = ?", params)
            conn.commit()
            return await self.get(video_id)
        data = self._load_data()
        if video_id in data["generations"]:
            generation = data["generations"][video_id]
            generation.update(updates)
            generation["updated_at"] = local_iso_time()
            data["generations"][video_id] = generation
            self._save_data(data)
            return generation
        return None

    async def delete(self, video_id: str) -> bool:
        """Delete a video generation"""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute("DELETE FROM video_generations WHERE video_id = ?", (video_id,))
            conn.commit()
            return cursor.rowcount > 0
        data = self._load_data()
        if video_id in data["generations"]:
            del data["generations"][video_id]
            self._save_data(data)
            return True
        return False


video_store = VideoStore()
