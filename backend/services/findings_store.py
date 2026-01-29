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
        self.data_dir = data_dir
        self.findings_dir = data_dir / "findings"
        self.findings_dir.mkdir(parents=True, exist_ok=True)
    
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
        
        findings = self._load_findings(notebook_id)
        findings[finding_id] = finding
        self._save_findings(notebook_id, findings)
        
        print(f"[FindingsStore] Created finding {finding_id}: {title}")
        return finding
    
    async def get_finding(self, notebook_id: str, finding_id: str) -> Optional[Finding]:
        """Get a specific finding."""
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
        findings = self._load_findings(notebook_id)
        
        # Apply filters
        result = list(findings.values())
        
        if type_filter:
            result = [f for f in result if f.type == type_filter]
        
        if starred_only:
            result = [f for f in result if f.starred]
        
        if tag_filter:
            result = [f for f in result if tag_filter in f.tags]
        
        # Sort by updated_at descending (newest first)
        result.sort(key=lambda f: f.updated_at, reverse=True)
        
        # Apply pagination
        return result[offset:offset + limit]
    
    async def update_finding(
        self,
        notebook_id: str,
        finding_id: str,
        updates: Dict[str, Any],
    ) -> Optional[Finding]:
        """Update a finding's title, tags, or starred status."""
        findings = self._load_findings(notebook_id)
        
        if finding_id not in findings:
            return None
        
        finding = findings[finding_id]
        
        # Update allowed fields
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
        findings = self._load_findings(notebook_id)
        
        if finding_id not in findings:
            return False
        
        del findings[finding_id]
        self._save_findings(notebook_id, findings)
        print(f"[FindingsStore] Deleted finding {finding_id}")
        return True
    
    async def get_stats(self, notebook_id: str) -> Dict[str, Any]:
        """Get statistics about findings for a notebook."""
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
