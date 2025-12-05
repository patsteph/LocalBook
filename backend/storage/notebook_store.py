"""Notebook storage"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
from config import settings

class NotebookStore:
    def __init__(self):
        self.storage_path = settings.data_dir / "notebooks.json"
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
        with open(self.storage_path, 'w') as f:
            json.dump(data, f, indent=2)

    async def list(self) -> List[Dict]:
        """List all notebooks with accurate source counts"""
        data = self._load_data()
        notebooks = list(data["notebooks"].values())
        
        # Load sources to get accurate counts
        sources_path = settings.data_dir / "sources.json"
        if sources_path.exists():
            with open(sources_path, 'r') as f:
                sources_data = json.load(f)
            
            # Count sources per notebook
            for notebook in notebooks:
                notebook_id = notebook["id"]
                count = sum(1 for s in sources_data.get("sources", {}).values() 
                           if s.get("notebook_id") == notebook_id)
                notebook["source_count"] = count
        
        return notebooks

    async def create(self, title: str, description: Optional[str] = None) -> Dict:
        """Create a new notebook"""
        notebook_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        notebook = {
            "id": notebook_id,
            "title": title,
            "description": description,
            "created_at": now,
            "updated_at": now,
            "source_count": 0
        }

        data = self._load_data()
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

notebook_store = NotebookStore()
