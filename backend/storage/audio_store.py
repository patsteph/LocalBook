"""Audio generation storage"""
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json


def local_iso_time() -> str:
    """Get current local time as ISO string"""
    return datetime.now().astimezone().isoformat()


class AudioStore:
    def __init__(self):
        self.storage_path = settings.data_dir / "audio_generations.json"
        self._ensure_storage()

    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"generations": {}})

    def _load_data(self) -> dict:
        """Load audio generations from storage"""
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save audio generations to storage"""
        atomic_write_json(self.storage_path, data)

    async def list(self, notebook_id: str) -> List[Dict]:
        """List all audio generations for a notebook"""
        data = self._load_data()
        generations = [
            g for g in data["generations"].values()
            if g.get("notebook_id") == notebook_id
        ]
        # Sort by created_at descending (newest first)
        generations.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return generations

    async def create(
        self,
        notebook_id: str,
        script: str = "",
        topic: str = "",
        duration_minutes: int = 10,
        host1_gender: str = "male",
        host2_gender: str = "female",
        accent: str = "us",
        skill_id: Optional[str] = None
    ) -> Dict:
        """Create a new audio generation record"""
        audio_id = str(uuid.uuid4())
        now = local_iso_time()

        generation = {
            "audio_id": audio_id,
            "notebook_id": notebook_id,
            "script": script,
            "topic": topic,
            "duration_minutes": duration_minutes,
            "host1_gender": host1_gender,
            "host2_gender": host2_gender,
            "accent": accent,
            "skill_id": skill_id,
            "audio_file_path": None,
            "duration_seconds": None,
            "status": "pending",
            "error_message": None,
            "created_at": now,
            "updated_at": now
        }

        data = self._load_data()
        data["generations"][audio_id] = generation
        self._save_data(data)

        return generation

    async def get(self, audio_id: str) -> Optional[Dict]:
        """Get an audio generation by ID"""
        data = self._load_data()
        return data["generations"].get(audio_id)

    async def get_by_notebook(self, notebook_id: str, audio_id: str) -> Optional[Dict]:
        """Get an audio generation by notebook and audio ID"""
        generation = await self.get(audio_id)
        if generation and generation.get("notebook_id") == notebook_id:
            return generation
        return None

    async def update(self, audio_id: str, updates: Dict) -> Optional[Dict]:
        """Update an audio generation"""
        data = self._load_data()
        if audio_id in data["generations"]:
            generation = data["generations"][audio_id]
            generation.update(updates)
            generation["updated_at"] = local_iso_time()
            data["generations"][audio_id] = generation
            self._save_data(data)
            return generation
        return None

    async def delete(self, audio_id: str) -> bool:
        """Delete an audio generation"""
        data = self._load_data()
        if audio_id in data["generations"]:
            del data["generations"][audio_id]
            self._save_data(data)
            return True
        return False


audio_store = AudioStore()
