"""Source storage"""
import json
import os
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json

class SourceStore:
    def __init__(self):
        self._use_sqlite = settings.use_sqlite
        self.storage_path = settings.data_dir / "sources.json"
        self._cache: Optional[dict] = None
        self._cache_mtime: float = 0.0
        if not self._use_sqlite:
            self._ensure_storage()

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"sources": {}})

    def _load_data(self) -> dict:
        """Load sources from storage with mtime-based caching.
        
        Only re-reads and re-parses the file when it has been modified.
        At scale (24+ notebooks, 1000s of sources) this eliminates
        redundant file I/O in loops that call list() per notebook.
        """
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
        """Save sources to storage and update cache"""
        atomic_write_json(self.storage_path, data)
        # Update cache immediately so subsequent reads don't re-parse
        self._cache = data
        try:
            self._cache_mtime = os.path.getmtime(self.storage_path)
        except OSError:
            self._cache_mtime = 0.0

    def _row_to_source(self, row) -> Dict:
        """Convert a SQLite row to a source dict, unpacking metadata_json."""
        d = dict(row)
        # Unpack tags from JSON string
        if isinstance(d.get('tags'), str):
            try:
                d['tags'] = json.loads(d['tags'])
            except (json.JSONDecodeError, TypeError):
                d['tags'] = []
        # Merge metadata_json into the dict
        meta = d.pop('metadata_json', None)
        if meta:
            try:
                extra = json.loads(meta) if isinstance(meta, str) else meta
                d.update(extra)
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    async def list(self, notebook_id: str) -> List[Dict]:
        """List all sources for a notebook"""
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT * FROM sources WHERE notebook_id = ?", (notebook_id,)
            ).fetchall()
            return [self._row_to_source(r) for r in rows]
        data = self._load_data()
        return [
            source for source in data["sources"].values()
            if source.get("notebook_id") == notebook_id
        ]

    async def list_all(self) -> Dict[str, List[Dict]]:
        """List ALL sources grouped by notebook_id (single file read).
        
        Use this instead of looping over notebooks calling list() individually.
        Returns {notebook_id: [source, ...]}
        """
        if self._use_sqlite:
            rows = self._get_db().execute("SELECT * FROM sources").fetchall()
            grouped: Dict[str, List[Dict]] = {}
            for row in rows:
                src = self._row_to_source(row)
                nb_id = src.get("notebook_id")
                if nb_id:
                    grouped.setdefault(nb_id, []).append(src)
            return grouped
        data = self._load_data()
        grouped = {}
        for source in data["sources"].values():
            nb_id = source.get("notebook_id")
            if nb_id:
                if nb_id not in grouped:
                    grouped[nb_id] = []
                grouped[nb_id].append(source)
        return grouped

    async def count_by_notebook(self) -> Dict[str, int]:
        """Get source counts per notebook (single file read).
        
        Use this instead of looping over notebooks calling len(list(nb)) individually.
        Returns {notebook_id: count}
        """
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT notebook_id, COUNT(*) as cnt FROM sources GROUP BY notebook_id"
            ).fetchall()
            return {r['notebook_id']: r['cnt'] for r in rows}
        data = self._load_data()
        counts: Dict[str, int] = {}
        for source in data["sources"].values():
            nb_id = source.get("notebook_id")
            if nb_id:
                counts[nb_id] = counts.get(nb_id, 0) + 1
        return counts

    async def create(self, notebook_id: str, filename: str, metadata: dict) -> Dict:
        """Create a new source.
        
        If metadata contains an 'id' field, that ID will be used.
        Otherwise, a new UUID is generated.
        """
        source_id = metadata.get("id") or str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        source = {
            "id": source_id,
            "notebook_id": notebook_id,
            "filename": filename,
            "created_at": now,
            **metadata
        }
        source["id"] = source_id

        if self._use_sqlite:
            known_fields = {'id', 'notebook_id', 'filename', 'content', 'url', 'author',
                            'date', 'format', 'type', 'notes', 'notes_updated_at',
                            'tags', 'tags_updated_at', 'created_at'}
            extra = {k: v for k, v in source.items() if k not in known_fields}
            tags = json.dumps(source.get('tags', []))
            conn = self._get_db()
            conn.execute(
                """INSERT OR REPLACE INTO sources
                   (id, notebook_id, filename, content, url, author, date, format, type,
                    notes, notes_updated_at, tags, tags_updated_at, created_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, notebook_id, filename, source.get('content'),
                 source.get('url'), source.get('author'), source.get('date'),
                 source.get('format'), source.get('type'),
                 source.get('notes'), source.get('notes_updated_at'),
                 tags, source.get('tags_updated_at'), now, json.dumps(extra))
            )
            conn.commit()
        else:
            data = self._load_data()
            data["sources"][source_id] = source
            self._save_data(data)

        return source

    async def get(self, source_id: str) -> Optional[Dict]:
        """Get a source by ID"""
        if self._use_sqlite:
            row = self._get_db().execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            return self._row_to_source(row) if row else None
        data = self._load_data()
        return data["sources"].get(source_id)

    async def update(self, notebook_id: str, source_id: str, updates: Dict) -> Optional[Dict]:
        """Update a source"""
        if self._use_sqlite:
            known_cols = {'filename', 'content', 'url', 'author', 'date', 'format', 'type',
                          'notes', 'notes_updated_at', 'tags', 'tags_updated_at'}
            sets = []
            params = []
            extra_updates = {}
            for k, v in updates.items():
                if k in known_cols:
                    if k == 'tags' and isinstance(v, list):
                        sets.append(f"{k} = ?")
                        params.append(json.dumps(v))
                    else:
                        sets.append(f"{k} = ?")
                        params.append(v)
                elif k not in ('id', 'notebook_id', 'created_at'):
                    extra_updates[k] = v
            if extra_updates:
                # Merge into metadata_json
                row = self._get_db().execute("SELECT metadata_json FROM sources WHERE id = ? AND notebook_id = ?", (source_id, notebook_id)).fetchone()
                if row:
                    existing_meta = json.loads(row['metadata_json'] or '{}')
                    existing_meta.update(extra_updates)
                    sets.append("metadata_json = ?")
                    params.append(json.dumps(existing_meta))
            if not sets:
                return await self.get(source_id)
            params.extend([source_id, notebook_id])
            conn = self._get_db()
            conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id = ? AND notebook_id = ?", params)
            conn.commit()
            return await self.get(source_id)
        data = self._load_data()
        if source_id in data["sources"]:
            source = data["sources"][source_id]
            if source.get("notebook_id") == notebook_id:
                source.update(updates)
                data["sources"][source_id] = source
                self._save_data(data)
                return source
        return None

    async def delete(self, notebook_id: str, source_id: str) -> bool:
        """Delete a source"""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute("DELETE FROM sources WHERE id = ? AND notebook_id = ?", (source_id, notebook_id))
            conn.commit()
            return cursor.rowcount > 0
        data = self._load_data()
        if source_id in data["sources"]:
            source = data["sources"][source_id]
            if source.get("notebook_id") == notebook_id:
                del data["sources"][source_id]
                self._save_data(data)
                return True
        return False

    async def delete_all(self, notebook_id: str) -> int:
        """Delete all sources for a notebook"""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute("DELETE FROM sources WHERE notebook_id = ?", (notebook_id,))
            conn.commit()
            return cursor.rowcount
        data = self._load_data()
        to_delete = [
            sid for sid, s in data["sources"].items() 
            if s.get("notebook_id") == notebook_id
        ]
        for sid in to_delete:
            del data["sources"][sid]
        if to_delete:
            self._save_data(data)
        return len(to_delete)

    async def get_content(self, notebook_id: str, source_id: str) -> Optional[Dict]:
        """Get source content for viewing"""
        source = await self.get(source_id)
        if source and source.get("notebook_id") == notebook_id:
            # Return source with content field - format matches frontend SourceContent interface
            return {
                "id": source["id"],
                "filename": source.get("filename", "Unknown"),
                "format": source.get("format", source.get("type", "unknown")),
                "content": source.get("content", ""),
                "url": source.get("url"),
                "author": source.get("author"),
                "date": source.get("date")
            }
        return None

    async def get_notes(self, notebook_id: str, source_id: str) -> str:
        """Get notes for a source - returns string, not list"""
        source = await self.get(source_id)
        if source and source.get("notebook_id") == notebook_id:
            return source.get("notes", "")
        return ""

    async def save_notes(self, notebook_id: str, source_id: str, notes: str) -> bool:
        """Save notes for a source"""
        if self._use_sqlite:
            now = datetime.utcnow().isoformat()
            conn = self._get_db()
            cursor = conn.execute(
                "UPDATE sources SET notes = ?, notes_updated_at = ? WHERE id = ? AND notebook_id = ?",
                (notes, now, source_id, notebook_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        data = self._load_data()
        if source_id in data["sources"]:
            source = data["sources"][source_id]
            if source.get("notebook_id") == notebook_id:
                source["notes"] = notes
                source["notes_updated_at"] = datetime.utcnow().isoformat()
                data["sources"][source_id] = source
                self._save_data(data)
                return True
        return False

    async def create_note(self, notebook_id: str, source_id: str, content: str, position: dict) -> Dict:
        """Create a note on a source - legacy method"""
        # This is kept for backward compatibility but notes are now stored as a single string
        return {
            "id": str(uuid.uuid4()),
            "source_id": source_id,
            "content": content,
            "position": position,
            "created_at": datetime.utcnow().isoformat()
        }

    # =========================================================================
    # Document Tagging (v0.6.0)
    # =========================================================================
    
    async def get_tags(self, notebook_id: str, source_id: str) -> List[str]:
        """Get tags for a source"""
        source = await self.get(source_id)
        if source and source.get("notebook_id") == notebook_id:
            return source.get("tags", [])
        return []
    
    async def set_tags(self, notebook_id: str, source_id: str, tags: List[str]) -> bool:
        """Set tags for a source (replaces existing tags)"""
        normalized_tags = list(set(tag.strip().lower() for tag in tags if tag.strip()))
        if self._use_sqlite:
            now = datetime.utcnow().isoformat()
            conn = self._get_db()
            cursor = conn.execute(
                "UPDATE sources SET tags = ?, tags_updated_at = ? WHERE id = ? AND notebook_id = ?",
                (json.dumps(normalized_tags), now, source_id, notebook_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        data = self._load_data()
        if source_id in data["sources"]:
            source = data["sources"][source_id]
            if source.get("notebook_id") == notebook_id:
                source["tags"] = normalized_tags
                source["tags_updated_at"] = datetime.utcnow().isoformat()
                data["sources"][source_id] = source
                self._save_data(data)
                return True
        return False
    
    async def add_tag(self, notebook_id: str, source_id: str, tag: str) -> bool:
        """Add a single tag to a source"""
        current_tags = await self.get_tags(notebook_id, source_id)
        normalized_tag = tag.strip().lower()
        if normalized_tag and normalized_tag not in current_tags:
            current_tags.append(normalized_tag)
            return await self.set_tags(notebook_id, source_id, current_tags)
        return True  # Tag already exists or empty
    
    async def remove_tag(self, notebook_id: str, source_id: str, tag: str) -> bool:
        """Remove a single tag from a source"""
        current_tags = await self.get_tags(notebook_id, source_id)
        normalized_tag = tag.strip().lower()
        if normalized_tag in current_tags:
            current_tags.remove(normalized_tag)
            return await self.set_tags(notebook_id, source_id, current_tags)
        return True  # Tag didn't exist
    
    async def get_all_tags(self, notebook_id: str) -> List[Dict]:
        """Get all unique tags used in a notebook with counts"""
        if self._use_sqlite:
            rows = self._get_db().execute(
                "SELECT tags FROM sources WHERE notebook_id = ? AND tags != '[]'",
                (notebook_id,)
            ).fetchall()
            tag_counts: Dict[str, int] = {}
            for row in rows:
                try:
                    tags = json.loads(row['tags']) if isinstance(row['tags'], str) else row['tags']
                    for tag in (tags or []):
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass
            return [
                {"tag": tag, "count": count}
                for tag, count in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))
            ]
        data = self._load_data()
        tag_counts = {}
        for source in data["sources"].values():
            if source.get("notebook_id") == notebook_id:
                for tag in source.get("tags", []):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        return [
            {"tag": tag, "count": count}
            for tag, count in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))
        ]
    
    async def get_sources_by_tag(self, notebook_id: str, tag: str) -> List[Dict]:
        """Get all sources with a specific tag"""
        normalized_tag = tag.strip().lower()
        if self._use_sqlite:
            # SQLite JSON: search for tag in the tags JSON array
            rows = self._get_db().execute(
                "SELECT * FROM sources WHERE notebook_id = ? AND tags LIKE ?",
                (notebook_id, f'%"{normalized_tag}"%')
            ).fetchall()
            return [self._row_to_source(r) for r in rows]
        data = self._load_data()
        return [
            source for source in data["sources"].values()
            if source.get("notebook_id") == notebook_id
            and normalized_tag in source.get("tags", [])
        ]

source_store = SourceStore()
