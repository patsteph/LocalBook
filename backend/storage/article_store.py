"""article_store — SQLite wrapper for the articles table.

Phase 1 of Tier 2 (2026-06-09). Schema lives in `database.py`. This module
provides typed CRUD + the few query patterns Correspondent needs:

  - list_by_source(source_id)
  - list_recent(notebook_id?, limit)
  - list_by_sender(sender_email, limit)
  - delete_by_source(source_id) — cascade hook for source deletion
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ArticleStore:
    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        if row is None:
            return {}
        d = dict(row)
        # Decode topic_tags JSON
        raw = d.get("topic_tags") or "[]"
        try:
            d["topic_tags"] = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            d["topic_tags"] = []
        return d

    async def create(
        self,
        *,
        source_id: str,
        notebook_id: str,
        position: int,
        title: str,
        body_text: str,
        body_html: Optional[str] = None,
        summary: Optional[str] = None,
        topic_tags: Optional[List[str]] = None,
        sender: Optional[str] = None,
    ) -> str:
        """Insert one article. Returns the new article_id."""
        article_id = str(uuid.uuid4())
        conn = self._get_db()
        conn.execute(
            """INSERT INTO articles
               (id, source_id, notebook_id, position, title, body_text, body_html,
                summary, topic_tags, sender, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article_id, source_id, notebook_id, position,
                (title or "")[:500],
                body_text,
                body_html,
                summary,
                json.dumps(topic_tags or []),
                sender,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return article_id

    async def create_batch(
        self,
        source_id: str,
        notebook_id: str,
        sender: Optional[str],
        articles: List[Dict[str, Any]],
    ) -> int:
        """Bulk insert. `articles` is a list of dicts with position, title,
        body_text, body_html (optional). Returns count inserted."""
        if not articles:
            return 0
        conn = self._get_db()
        now = datetime.utcnow().isoformat()
        rows = []
        for a in articles:
            rows.append((
                str(uuid.uuid4()),
                source_id,
                notebook_id,
                int(a.get("position", 0)),
                (a.get("title") or "")[:500],
                a.get("body_text") or "",
                a.get("body_html"),
                a.get("summary"),
                json.dumps(a.get("topic_tags") or []),
                sender,
                now,
            ))
        conn.executemany(
            """INSERT INTO articles
               (id, source_id, notebook_id, position, title, body_text, body_html,
                summary, topic_tags, sender, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        return len(rows)

    async def list_by_source(self, source_id: str) -> List[Dict[str, Any]]:
        try:
            rows = self._get_db().execute(
                "SELECT * FROM articles WHERE source_id = ? ORDER BY position ASC",
                (source_id,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[article_store.list_by_source] {e}")
            return []

    async def list_recent(
        self,
        *,
        notebook_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Most recent articles, newest first. Optionally scoped to one notebook."""
        try:
            if notebook_id:
                rows = self._get_db().execute(
                    "SELECT * FROM articles WHERE notebook_id = ? ORDER BY created_at DESC LIMIT ?",
                    (notebook_id, int(limit)),
                ).fetchall()
            else:
                rows = self._get_db().execute(
                    "SELECT * FROM articles ORDER BY created_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[article_store.list_recent] {e}")
            return []

    async def list_by_sender(
        self,
        sender_query: str,
        *,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """List articles where the sender matches (case-insensitive LIKE).
        Used by `@correspondent show articles from <sender>`."""
        try:
            pattern = f"%{sender_query.strip().lower()}%"
            rows = self._get_db().execute(
                "SELECT * FROM articles WHERE LOWER(sender) LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (pattern, int(limit)),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[article_store.list_by_sender] {e}")
            return []

    async def get(self, article_id: str) -> Optional[Dict[str, Any]]:
        try:
            row = self._get_db().execute(
                "SELECT * FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None
        except Exception as e:
            logger.debug(f"[article_store.get] {e}")
            return None

    async def count_by_source(self, source_id: str) -> int:
        try:
            row = self._get_db().execute(
                "SELECT COUNT(*) as c FROM articles WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            return int(row["c"]) if row else 0
        except Exception as e:
            logger.debug(f"[article_store.count_by_source] {e}")
            return 0

    async def delete_by_source(self, source_id: str) -> int:
        """Cascade delete: called when parent source is removed."""
        try:
            conn = self._get_db()
            cur = conn.execute("DELETE FROM articles WHERE source_id = ?", (source_id,))
            conn.commit()
            return cur.rowcount
        except Exception as e:
            logger.warning(f"[article_store.delete_by_source] {e}")
            return 0


# Singleton
article_store = ArticleStore()
