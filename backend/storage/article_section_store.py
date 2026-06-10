"""article_section_store — Phase 14.D (2026-06-10).

Per-notebook subject sections for articles ("AI accounting", "Payments",
etc.). NOT the same as the GLOBAL `notebook_sections` table which groups
notebooks themselves in the left-nav.

Articles get classified into the best-fit existing section, or a new
section is auto-created when confidence ≥ 0.85. Low-confidence proposals
sit on the article row as `section_proposal` text for later review.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ArticleSectionStore:
    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    async def list_for_notebook(self, notebook_id: str) -> List[Dict[str, Any]]:
        """All article sections for a notebook, ordered by article_count desc
        (most-populated first) — useful for the sectioner's prompt context."""
        try:
            rows = self._get_db().execute(
                """SELECT id, name, description, article_count, created_at
                   FROM article_sections
                   WHERE notebook_id = ?
                   ORDER BY article_count DESC, created_at ASC""",
                (notebook_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[article_section_store.list_for_notebook] {e}")
            return []

    async def find_by_name(self, notebook_id: str, name: str) -> Optional[Dict[str, Any]]:
        try:
            row = self._get_db().execute(
                "SELECT * FROM article_sections WHERE notebook_id = ? AND LOWER(name) = LOWER(?)",
                (notebook_id, name.strip()),
            ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.debug(f"[article_section_store.find_by_name] {e}")
            return None

    async def create(
        self,
        notebook_id: str,
        name: str,
        description: Optional[str] = None,
    ) -> str:
        """Create a new section. Returns section_id. Idempotent on name —
        if a section with the same name exists, returns the existing id."""
        existing = await self.find_by_name(notebook_id, name)
        if existing:
            return existing["id"]
        section_id = str(uuid.uuid4())
        conn = self._get_db()
        conn.execute(
            """INSERT INTO article_sections (id, notebook_id, name, description, created_at, article_count)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (section_id, notebook_id, name.strip()[:200], (description or "")[:500],
             datetime.utcnow().isoformat()),
        )
        conn.commit()
        return section_id

    async def increment_count(self, section_id: str) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                "UPDATE article_sections SET article_count = article_count + 1 WHERE id = ?",
                (section_id,),
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"[article_section_store.increment_count] {e}")


article_section_store = ArticleSectionStore()
