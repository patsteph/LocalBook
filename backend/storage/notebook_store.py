"""Notebook storage"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json

class NotebookStore:
    def __init__(self):
        self.storage_path = settings.data_dir / "notebooks.json"
        self._sources_count_cache: Optional[Dict[str, int]] = None
        self._sources_count_cache_mtime: Optional[float] = None
        self._ensure_storage()

    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"notebooks": {}})

    def _load_data(self) -> dict:
        """Load notebooks from storage"""
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save notebooks to storage"""
        atomic_write_json(self.storage_path, data)

    async def list(self) -> List[Dict]:
        """List all notebooks with accurate source counts"""
        data = self._load_data()
        notebooks = list(data["notebooks"].values())
        
        # Always read fresh source counts for UI accuracy
        sources_path = settings.data_dir / "sources.json"
        if sources_path.exists():
            try:
                with open(sources_path, 'r') as f:
                    sources_data = json.load(f)
                counts: Dict[str, int] = {}
                for s in sources_data.get("sources", {}).values():
                    nbid = s.get("notebook_id")
                    if nbid:
                        counts[nbid] = counts.get(nbid, 0) + 1

                for notebook in notebooks:
                    notebook_id = notebook["id"]
                    notebook["source_count"] = counts.get(notebook_id, 0)
            except Exception:
                pass
        
        return notebooks

    # Default color palette for notebooks
    DEFAULT_COLORS = [
        "#3B82F6",  # Blue
        "#10B981",  # Green
        "#F59E0B",  # Amber
        "#EF4444",  # Red
        "#8B5CF6",  # Purple
        "#EC4899",  # Pink
        "#06B6D4",  # Cyan
        "#F97316",  # Orange
    ]
    
    def _get_next_color(self, data: dict) -> str:
        """Get the next color in rotation for a new notebook"""
        used_colors = [n.get("color") for n in data["notebooks"].values() if n.get("color")]
        for color in self.DEFAULT_COLORS:
            if color not in used_colors:
                return color
        # If all colors used, start over
        return self.DEFAULT_COLORS[len(data["notebooks"]) % len(self.DEFAULT_COLORS)]

    async def create(self, title: str, description: Optional[str] = None, color: Optional[str] = None) -> Dict:
        """Create a new notebook"""
        notebook_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        data = self._load_data()
        
        # Auto-assign color if not provided
        if not color:
            color = self._get_next_color(data)

        notebook = {
            "id": notebook_id,
            "title": title,
            "description": description,
            "color": color,
            "created_at": now,
            "updated_at": now,
            "source_count": 0
        }

        data["notebooks"][notebook_id] = notebook
        self._save_data(data)

        return notebook

    async def get(self, notebook_id: str) -> Optional[Dict]:
        """Get a notebook by ID"""
        data = self._load_data()
        return data["notebooks"].get(notebook_id)

    async def delete(self, notebook_id: str) -> bool:
        """Delete a notebook"""
        data = self._load_data()
        if notebook_id in data["notebooks"]:
            del data["notebooks"][notebook_id]
            self._save_data(data)
            return True
        return False

    async def update(self, notebook_id: str, updates: dict) -> Optional[Dict]:
        """Update a notebook with the given updates"""
        data = self._load_data()
        if notebook_id in data["notebooks"]:
            notebook = data["notebooks"][notebook_id]
            notebook.update(updates)
            notebook["updated_at"] = datetime.utcnow().isoformat()
            data["notebooks"][notebook_id] = notebook
            self._save_data(data)
            return notebook
        return None

    async def update_source_count(self, notebook_id: str, count: int):
        """Update the source count for a notebook"""
        data = self._load_data()
        if notebook_id in data["notebooks"]:
            data["notebooks"][notebook_id]["source_count"] = count
            data["notebooks"][notebook_id]["updated_at"] = datetime.utcnow().isoformat()
            self._save_data(data)
    
    async def update_color(self, notebook_id: str, color: str) -> Optional[Dict]:
        """Update a notebook's color"""
        return await self.update(notebook_id, {"color": color})
    
    def get_color_palette(self) -> List[str]:
        """Get the available color palette"""
        return self.DEFAULT_COLORS

notebook_store = NotebookStore()
