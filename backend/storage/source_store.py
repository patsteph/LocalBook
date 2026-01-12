"""Source storage"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json

class SourceStore:
    def __init__(self):
        self.storage_path = settings.data_dir / "sources.json"
        self._ensure_storage()

    def _ensure_storage(self):
        """Ensure storage file exists"""
        if not self.storage_path.exists():
            self._save_data({"sources": {}})

    def _load_data(self) -> dict:
        """Load sources from storage"""
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save sources to storage"""
        atomic_write_json(self.storage_path, data)

    async def list(self, notebook_id: str) -> List[Dict]:
        """List all sources for a notebook"""
        data = self._load_data()
        return [
            source for source in data["sources"].values()
            if source.get("notebook_id") == notebook_id
        ]

    async def create(self, notebook_id: str, filename: str, metadata: dict) -> Dict:
        """Create a new source.
        
        If metadata contains an 'id' field, that ID will be used.
        Otherwise, a new UUID is generated.
        """
        # Use provided ID or generate new one
        source_id = metadata.get("id") or str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        source = {
            "id": source_id,
            "notebook_id": notebook_id,
            "filename": filename,
            "created_at": now,
            **metadata
        }
        # Ensure id field matches the key (in case metadata had a different id)
        source["id"] = source_id

        data = self._load_data()
        data["sources"][source_id] = source
        self._save_data(data)

        return source

    async def get(self, source_id: str) -> Optional[Dict]:
        """Get a source by ID"""
        data = self._load_data()
        return data["sources"].get(source_id)

    async def update(self, notebook_id: str, source_id: str, updates: Dict) -> Optional[Dict]:
        """Update a source"""
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
        data = self._load_data()
        if source_id in data["sources"]:
            source = data["sources"][source_id]
            if source.get("notebook_id") == notebook_id:
                del data["sources"][source_id]
                self._save_data(data)
                return True
        return False

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
        data = self._load_data()
        if source_id in data["sources"]:
            source = data["sources"][source_id]
            if source.get("notebook_id") == notebook_id:
                # Normalize tags: lowercase, strip whitespace, remove duplicates
                normalized_tags = list(set(tag.strip().lower() for tag in tags if tag.strip()))
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
        data = self._load_data()
        normalized_tag = tag.strip().lower()
        return [
            source for source in data["sources"].values()
            if source.get("notebook_id") == notebook_id
            and normalized_tag in source.get("tags", [])
        ]

source_store = SourceStore()
