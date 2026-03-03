"""Notebook storage"""
import json
import os
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json

class NotebookStore:
    def __init__(self):
        self._use_sqlite = settings.use_sqlite
        self.storage_path = settings.data_dir / "notebooks.json"
        self._cache: Optional[dict] = None
        self._cache_mtime: float = 0.0
        self._sources_count_cache: Optional[Dict[str, int]] = None
        self._sources_count_cache_mtime: Optional[float] = None
        if not self._use_sqlite:
            self._ensure_storage()

    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"notebooks": {}})

    def _load_data(self) -> dict:
        """Load notebooks from storage with mtime-based caching."""
        try:
            current_mtime = os.path.getmtime(self.storage_path)
        except OSError:
            current_mtime = 0.0
        
        if self._cache is not None and current_mtime == self._cache_mtime:
            return self._cache
        
        with open(self.storage_path, 'r') as f:
            self._cache = json.load(f)
        self._cache_mtime = current_mtime
        return self._cache

    def _save_data(self, data: dict):
        """Save notebooks to storage and update cache"""
        atomic_write_json(self.storage_path, data)
        self._cache = data
        try:
            self._cache_mtime = os.path.getmtime(self.storage_path)
        except OSError:
            self._cache_mtime = 0.0

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    async def list(self) -> List[Dict]:
        """List all notebooks with accurate source counts"""
        if self._use_sqlite:
            conn = self._get_db()
            rows = conn.execute("SELECT * FROM notebooks ORDER BY created_at DESC").fetchall()
            notebooks = [dict(row) for row in rows]
        else:
            data = self._load_data()
            notebooks = list(data["notebooks"].values())
        
        # Use source_store's cached data instead of raw file read
        try:
            from storage.source_store import source_store
            counts = await source_store.count_by_notebook()
            for notebook in notebooks:
                notebook["source_count"] = counts.get(notebook["id"], 0)
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
        
        if self._use_sqlite:
            if not color:
                conn = self._get_db()
                used = [r['color'] for r in conn.execute("SELECT color FROM notebooks WHERE color IS NOT NULL").fetchall()]
                color = next((c for c in self.DEFAULT_COLORS if c not in used), self.DEFAULT_COLORS[0])
            notebook = {
                "id": notebook_id, "title": title, "description": description,
                "color": color, "created_at": now, "updated_at": now, "source_count": 0
            }
            conn = self._get_db()
            conn.execute(
                """INSERT INTO notebooks (id, title, description, color, source_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)""",
                (notebook_id, title, description, color, now, now)
            )
            conn.commit()
            return notebook
        
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
        if self._use_sqlite:
            row = self._get_db().execute("SELECT * FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
            return dict(row) if row else None
        data = self._load_data()
        return data["notebooks"].get(notebook_id)

    async def delete(self, notebook_id: str) -> bool:
        """Delete a notebook"""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))
            conn.commit()
            return cursor.rowcount > 0
        data = self._load_data()
        if notebook_id in data["notebooks"]:
            del data["notebooks"][notebook_id]
            self._save_data(data)
            return True
        return False

    async def update(self, notebook_id: str, updates: dict) -> Optional[Dict]:
        """Update a notebook with the given updates"""
        if self._use_sqlite:
            now = datetime.utcnow().isoformat()
            allowed = {'title', 'description', 'color', 'source_count', 'section_id', 'sort_order'}
            sets = []
            params = []
            for k, v in updates.items():
                if k in allowed:
                    sets.append(f"{k} = ?")
                    params.append(v)
            if not sets:
                return await self.get(notebook_id)
            sets.append("updated_at = ?")
            params.append(now)
            params.append(notebook_id)
            conn = self._get_db()
            conn.execute(f"UPDATE notebooks SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
            return await self.get(notebook_id)
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
        if self._use_sqlite:
            conn = self._get_db()
            conn.execute(
                "UPDATE notebooks SET source_count = ?, updated_at = ? WHERE id = ?",
                (count, datetime.utcnow().isoformat(), notebook_id)
            )
            conn.commit()
            return
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

    async def list_sections(self) -> List[Dict]:
        """List all notebook sections ordered by sort_order"""
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT * FROM notebook_sections ORDER BY sort_order, created_at"
            ).fetchall()
            return [{**dict(r), 'collapsed': bool(r['collapsed'])} for r in rows]
        return []

    async def create_section(self, name: str) -> Dict:
        """Create a new notebook section"""
        section_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        if self._use_sqlite:
            conn = self._get_db()
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM notebook_sections").fetchone()[0]
            section = {
                "id": section_id, "name": name,
                "sort_order": max_order + 1, "collapsed": False, "created_at": now
            }
            conn.execute(
                "INSERT INTO notebook_sections (id, name, sort_order, collapsed, created_at) VALUES (?, ?, ?, 0, ?)",
                (section_id, name, max_order + 1, now)
            )
            conn.commit()
            return section
        return {"id": section_id, "name": name, "sort_order": 0, "collapsed": False, "created_at": now}

    async def update_section(self, section_id: str, updates: dict) -> Optional[Dict]:
        """Update a section's name or collapsed state"""
        if self._use_sqlite:
            sets = []
            params = []
            if "name" in updates:
                sets.append("name = ?")
                params.append(updates["name"])
            if "collapsed" in updates:
                sets.append("collapsed = ?")
                params.append(1 if updates["collapsed"] else 0)
            if not sets:
                return None
            params.append(section_id)
            conn = self._get_db()
            cursor = conn.execute(f"UPDATE notebook_sections SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM notebook_sections WHERE id = ?", (section_id,)).fetchone()
            return {**dict(row), 'collapsed': bool(row['collapsed'])} if row else None
        return None

    async def delete_section(self, section_id: str) -> bool:
        """Delete a section; notebooks in it become unsectioned"""
        if self._use_sqlite:
            conn = self._get_db()
            conn.execute("UPDATE notebooks SET section_id = NULL WHERE section_id = ?", (section_id,))
            cursor = conn.execute("DELETE FROM notebook_sections WHERE id = ?", (section_id,))
            conn.commit()
            return cursor.rowcount > 0
        return False

    async def reorder_sections(self, section_ids: list):
        """Set sort_order based on position in the provided list"""
        if self._use_sqlite:
            conn = self._get_db()
            for idx, sid in enumerate(section_ids):
                conn.execute("UPDATE notebook_sections SET sort_order = ? WHERE id = ?", (idx, sid))
            conn.commit()

notebook_store = NotebookStore()
