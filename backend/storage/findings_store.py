"""
FindingsStore - Storage for user bookmarks, saved visuals, and highlights.

Part of the Canvas architecture - allows users to save and organize
key findings from their research across Chat, Studio, and sources.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict, field


@dataclass
class Finding:
    """A saved finding/bookmark from the user's research."""
    id: str
    notebook_id: str
    type: str  # 'visual' | 'answer' | 'highlight' | 'source' | 'note'
    title: str
    created_at: str
    updated_at: str
    content: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    starred: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Finding':
        return cls(**data)


class FindingsStore:
    """Store and retrieve user findings/bookmarks per notebook."""
    
    def __init__(self, data_dir: Path):
        from config import settings
        self._use_sqlite = settings.use_sqlite
        self.data_dir = data_dir
        self.findings_dir = data_dir / "findings"
        if not self._use_sqlite:
            self.findings_dir.mkdir(parents=True, exist_ok=True)

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    def _row_to_finding(self, row) -> Finding:
        """Convert a SQLite row to a Finding dataclass."""
        d = dict(row)
        content = {}
        if d.get('content_json'):
            try:
                content = json.loads(d['content_json']) if isinstance(d['content_json'], str) else d['content_json']
            except (json.JSONDecodeError, TypeError):
                pass
        tags = []
        if d.get('tags'):
            try:
                tags = json.loads(d['tags']) if isinstance(d['tags'], str) else d['tags']
            except (json.JSONDecodeError, TypeError):
                pass
        return Finding(
            id=d['id'],
            notebook_id=d['notebook_id'],
            type=d['type'],
            title=d['title'],
            created_at=d['created_at'],
            updated_at=d['updated_at'],
            content=content,
            tags=tags,
            starred=bool(d.get('starred', 0)),
        )
    
    def _get_notebook_file(self, notebook_id: str) -> Path:
        """Get the findings file for a notebook."""
        return self.findings_dir / f"{notebook_id}.json"
    
    def _load_findings(self, notebook_id: str) -> Dict[str, Finding]:
        """Load all findings for a notebook."""
        file_path = self._get_notebook_file(notebook_id)
        if not file_path.exists():
            return {}
        
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                return {k: Finding.from_dict(v) for k, v in data.items()}
        except Exception as e:
            print(f"[FindingsStore] Error loading findings: {e}")
            return {}
    
    def _save_findings(self, notebook_id: str, findings: Dict[str, Finding]):
        """Save all findings for a notebook."""
        file_path = self._get_notebook_file(notebook_id)
        try:
            with open(file_path, 'w') as f:
                data = {k: v.to_dict() for k, v in findings.items()}
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[FindingsStore] Error saving findings: {e}")
            raise
    
    async def create_finding(
        self,
        notebook_id: str,
        finding_type: str,
        title: str,
        content: Dict[str, Any],
        tags: Optional[List[str]] = None,
        starred: bool = False,
    ) -> Finding:
        """Create a new finding."""
        finding_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        finding = Finding(
            id=finding_id,
            notebook_id=notebook_id,
            type=finding_type,
            title=title,
            created_at=now,
            updated_at=now,
            content=content,
            tags=tags or [],
            starred=starred,
        )
        
        if self._use_sqlite:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO findings (id, notebook_id, type, title, content_json, tags, starred, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (finding_id, notebook_id, finding_type, title,
                 json.dumps(content), json.dumps(tags or []),
                 1 if starred else 0, now, now)
            )
            conn.commit()
        else:
            findings = self._load_findings(notebook_id)
            findings[finding_id] = finding
            self._save_findings(notebook_id, findings)
        
        print(f"[FindingsStore] Created finding {finding_id}: {title}")
        return finding
    
    async def get_finding(self, notebook_id: str, finding_id: str) -> Optional[Finding]:
        """Get a specific finding."""
        if self._use_sqlite:
            row = self._get_db().execute(
                "SELECT * FROM findings WHERE id = ? AND notebook_id = ?",
                (finding_id, notebook_id)
            ).fetchone()
            return self._row_to_finding(row) if row else None
        findings = self._load_findings(notebook_id)
        return findings.get(finding_id)
    
    async def get_findings(
        self,
        notebook_id: str,
        type_filter: Optional[str] = None,
        starred_only: bool = False,
        tag_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Finding]:
        """Get findings for a notebook with optional filters."""
        if self._use_sqlite:
            query = "SELECT * FROM findings WHERE notebook_id = ?"
            params: list = [notebook_id]
            if type_filter:
                query += " AND type = ?"
                params.append(type_filter)
            if starred_only:
                query += " AND starred = 1"
            if tag_filter:
                query += " AND tags LIKE ?"
                params.append(f'%"{tag_filter}"%')
            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._get_db().execute(query, params).fetchall()
            return [self._row_to_finding(r) for r in rows]
        
        findings = self._load_findings(notebook_id)
        result = list(findings.values())
        
        if type_filter:
            result = [f for f in result if f.type == type_filter]
        if starred_only:
            result = [f for f in result if f.starred]
        if tag_filter:
            result = [f for f in result if tag_filter in f.tags]
        
        result.sort(key=lambda f: f.updated_at, reverse=True)
        return result[offset:offset + limit]
    
    async def update_finding(
        self,
        notebook_id: str,
        finding_id: str,
        updates: Dict[str, Any],
    ) -> Optional[Finding]:
        """Update a finding's title, tags, or starred status."""
        if self._use_sqlite:
            now = datetime.utcnow().isoformat()
            sets = ["updated_at = ?"]
            params: list = [now]
            if 'title' in updates:
                sets.append("title = ?")
                params.append(updates['title'])
            if 'tags' in updates:
                sets.append("tags = ?")
                params.append(json.dumps(updates['tags']))
            if 'starred' in updates:
                sets.append("starred = ?")
                params.append(1 if updates['starred'] else 0)
            if 'content' in updates:
                # Merge content updates
                existing = await self.get_finding(notebook_id, finding_id)
                if existing:
                    existing.content.update(updates['content'])
                    sets.append("content_json = ?")
                    params.append(json.dumps(existing.content))
            params.extend([finding_id, notebook_id])
            conn = self._get_db()
            conn.execute(
                f"UPDATE findings SET {', '.join(sets)} WHERE id = ? AND notebook_id = ?",
                params
            )
            conn.commit()
            print(f"[FindingsStore] Updated finding {finding_id}")
            return await self.get_finding(notebook_id, finding_id)
        
        findings = self._load_findings(notebook_id)
        if finding_id not in findings:
            return None
        
        finding = findings[finding_id]
        if 'title' in updates:
            finding.title = updates['title']
        if 'tags' in updates:
            finding.tags = updates['tags']
        if 'starred' in updates:
            finding.starred = updates['starred']
        if 'content' in updates:
            finding.content.update(updates['content'])
        
        finding.updated_at = datetime.utcnow().isoformat()
        self._save_findings(notebook_id, findings)
        print(f"[FindingsStore] Updated finding {finding_id}")
        return finding
    
    async def delete_finding(self, notebook_id: str, finding_id: str) -> bool:
        """Delete a finding."""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute(
                "DELETE FROM findings WHERE id = ? AND notebook_id = ?",
                (finding_id, notebook_id)
            )
            conn.commit()
            if cursor.rowcount > 0:
                print(f"[FindingsStore] Deleted finding {finding_id}")
                return True
            return False
        
        findings = self._load_findings(notebook_id)
        if finding_id not in findings:
            return False
        
        del findings[finding_id]
        self._save_findings(notebook_id, findings)
        print(f"[FindingsStore] Deleted finding {finding_id}")
        return True
    
    async def get_stats(self, notebook_id: str) -> Dict[str, Any]:
        """Get statistics about findings for a notebook."""
        if self._use_sqlite:
            conn = self._get_db()
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM findings WHERE notebook_id = ?", (notebook_id,)
            ).fetchone()['cnt']
            starred = conn.execute(
                "SELECT COUNT(*) as cnt FROM findings WHERE notebook_id = ? AND starred = 1", (notebook_id,)
            ).fetchone()['cnt']
            type_rows = conn.execute(
                "SELECT type, COUNT(*) as cnt FROM findings WHERE notebook_id = ? GROUP BY type", (notebook_id,)
            ).fetchall()
            type_counts = {r['type']: r['cnt'] for r in type_rows}
            return {'total': total, 'by_type': type_counts, 'starred': starred}
        
        findings = self._load_findings(notebook_id)
        type_counts = {}
        starred_count = 0
        for finding in findings.values():
            type_counts[finding.type] = type_counts.get(finding.type, 0) + 1
            if finding.starred:
                starred_count += 1
        return {
            'total': len(findings),
            'by_type': type_counts,
            'starred': starred_count,
        }
    
    async def delete_notebook_findings(self, notebook_id: str) -> bool:
        """Delete all findings for a notebook (when notebook is deleted)."""
        if self._use_sqlite:
            conn = self._get_db()
            cursor = conn.execute(
                "DELETE FROM findings WHERE notebook_id = ?", (notebook_id,)
            )
            conn.commit()
            if cursor.rowcount > 0:
                print(f"[FindingsStore] Deleted all findings for notebook {notebook_id}")
                return True
            return False
        file_path = self._get_notebook_file(notebook_id)
        if file_path.exists():
            file_path.unlink()
            print(f"[FindingsStore] Deleted all findings for notebook {notebook_id}")
            return True
        return False


# Singleton instance - initialized in main.py
findings_store: Optional[FindingsStore] = None


def init_findings_store(data_dir: Path) -> FindingsStore:
    """Initialize the findings store."""
    global findings_store
    findings_store = FindingsStore(data_dir)
    print(f"[FindingsStore] Initialized at {data_dir / 'findings'}")
    return findings_store


def get_findings_store() -> FindingsStore:
    """Get the findings store instance."""
    if findings_store is None:
        raise RuntimeError("FindingsStore not initialized. Call init_findings_store first.")
    return findings_store
