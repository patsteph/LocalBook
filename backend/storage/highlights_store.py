"""Highlights storage for source annotations"""
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json


class HighlightsStore:
    def __init__(self):
        self._use_sqlite = settings.use_sqlite
        self.storage_path = settings.data_dir / "highlights.json"
        if not self._use_sqlite:
            self._ensure_storage()

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"highlights": {}})

    def _load_data(self) -> dict:
        """Load highlights from storage"""
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save highlights to storage"""
        atomic_write_json(self.storage_path, data)

    async def list(self, notebook_id: str, source_id: str) -> List[Dict]:
        """List all highlights for a source"""
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT * FROM highlights WHERE notebook_id = ? AND source_id = ?",
                (notebook_id, source_id)
            ).fetchall()
            return [dict(r) for r in rows]
        data = self._load_data()
        return [
            h for h in data["highlights"].values()
            if h.get("notebook_id") == notebook_id and h.get("source_id") == source_id
        ]
    
    async def list_by_notebook(self, notebook_id: str) -> List[Dict]:
        """List all highlights across all sources in a notebook"""
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT * FROM highlights WHERE notebook_id = ?", (notebook_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        data = self._load_data()
        return [
            h for h in data["highlights"].values()
            if h.get("notebook_id") == notebook_id
        ]

    async def create(
        self,
        notebook_id: str,
        source_id: str,
        start_offset: int,
        end_offset: int,
        highlighted_text: str,
        color: str = "yellow",
        annotation: str = ""
    ) -> Dict:
        """Create a new highlight"""
        highlight_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        highlight = {
            "highlight_id": highlight_id,
            "notebook_id": notebook_id,
            "source_id": source_id,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "highlighted_text": highlighted_text,
            "color": color,
            "annotation": annotation,
            "created_at": now,
            "updated_at": now
        }

        if self._use_sqlite:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO highlights
                   (highlight_id, notebook_id, source_id, start_offset, end_offset,
                    highlighted_text, color, annotation, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (highlight_id, notebook_id, source_id, start_offset, end_offset,
                 highlighted_text, color, annotation, now, now)
            )
            conn.commit()
        else:
            data = self._load_data()
            data["highlights"][highlight_id] = highlight
            self._save_data(data)

        try:
            from services.event_logger import log_highlight
            log_highlight(notebook_id, source_id, highlighted_text, annotation or None)
        except Exception:
            pass

        return highlight

    async def get(self, highlight_id: str) -> Optional[Dict]:
        """Get a highlight by ID"""
        if self._use_sqlite:
            row = self._get_db().execute(
                "SELECT * FROM highlights WHERE highlight_id = ?", (highlight_id,)
            ).fetchone()
            return dict(row) if row else None
        data = self._load_data()
        return data["highlights"].get(highlight_id)

    async def update(self, highlight_id: str, updates: Dict) -> Optional[Dict]:
        """Update a highlight"""
        if self._use_sqlite:
            now = datetime.utcnow().isoformat()
            allowed = {'color', 'annotation', 'highlighted_text', 'start_offset', 'end_offset'}
            sets = []
            params = []
            for k, v in updates.items():
                if k in allowed:
                    sets.append(f"{k} = ?")
                    params.append(v)
            if not sets:
                return await self.get(highlight_id)
            sets.append("updated_at = ?")
            params.append(now)
            params.append(highlight_id)
            conn = self._get_db()
            conn.execute(f"UPDATE highlights SET {', '.join(sets)} WHERE highlight_id = ?", params)
            conn.commit()
            return await self.get(highlight_id)
        data = self._load_data()
        if highlight_id in data["highlights"]:
            highlight = data["highlights"][highlight_id]
            highlight.update(updates)
            highlight["updated_at"] = datetime.utcnow().isoformat()
            data["highlights"][highlight_id] = highlight
            self._save_data(data)
            return highlight
        return None

    async def delete(self, highlight_id: str) -> bool:
        """Delete a highlight"""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute("DELETE FROM highlights WHERE highlight_id = ?", (highlight_id,))
            conn.commit()
            return cursor.rowcount > 0
        data = self._load_data()
        if highlight_id in data["highlights"]:
            del data["highlights"][highlight_id]
            self._save_data(data)
            return True
        return False

    async def delete_for_source(self, notebook_id: str, source_id: str) -> int:
        """Delete all highlights for a source, returns count deleted"""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute(
                "DELETE FROM highlights WHERE notebook_id = ? AND source_id = ?",
                (notebook_id, source_id)
            )
            conn.commit()
            return cursor.rowcount
        data = self._load_data()
        to_delete = [
            hid for hid, h in data["highlights"].items()
            if h.get("notebook_id") == notebook_id and h.get("source_id") == source_id
        ]
        for hid in to_delete:
            del data["highlights"][hid]
        self._save_data(data)
        return len(to_delete)


highlights_store = HighlightsStore()
