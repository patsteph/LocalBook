"""Canvas Notes storage — persisted rich editor notes."""
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict
import logging

logger = logging.getLogger(__name__)


class NoteStore:
    """CRUD for canvas_notes table.

    Notes are living documents that persist between sessions.
    They are separate from sources — a note only enters the RAG pipeline
    when the user explicitly clicks 'Save as Source'.
    """

    def _get_db(self):
        from storage.database import get_db
        return get_db().get_connection()

    def _row_to_note(self, row) -> Dict:
        """Convert a SQLite row to a note dict, unpacking JSON fields."""
        d = dict(row)
        for json_field in ('tags', 'original_image_paths', 'wikilinks_out'):
            if isinstance(d.get(json_field), str):
                try:
                    d[json_field] = json.loads(d[json_field])
                except (json.JSONDecodeError, TypeError):
                    d[json_field] = []
        return d

    # =========================================================================
    # CRUD
    # =========================================================================

    async def create(
        self,
        *,
        note_id: Optional[str] = None,
        notebook_id: Optional[str] = None,
        title: str = '',
        content_markdown: str = '',
        content_blocknote_json: str = '{}',
        source_type: str = 'typed',
        note_type: str = 'note',
        tags: Optional[List[str]] = None,
        voice_weight: float = 1.0,
        original_image_paths: Optional[List[str]] = None,
    ) -> Dict:
        """Create a new canvas note and return it."""
        now = datetime.utcnow().isoformat()
        nid = note_id or str(uuid.uuid4())
        tags_json = json.dumps(tags or [])

        conn = self._get_db()
        conn.execute(
            """
            INSERT INTO canvas_notes
                (id, notebook_id, title, content_markdown, content_blocknote_json,
                 source_type, note_type, tags, voice_weight,
                 original_image_paths, wikilinks_out, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)
            """,
            (nid, notebook_id, title, content_markdown, content_blocknote_json,
             source_type, note_type, tags_json, voice_weight, json.dumps(original_image_paths or []), now, now),
        )
        conn.commit()

        # Record voice observation for Sprint 4 pipeline (non-fatal)
        self._maybe_record_observation(
            text=content_markdown,
            source_type=source_type,
            weight=voice_weight,
            notebook_id=notebook_id,
            note_id=nid,
        )

        return await self.get(nid)  # type: ignore[return-value]

    async def get(self, note_id: str) -> Optional[Dict]:
        """Fetch a single note by ID."""
        row = self._get_db().execute(
            "SELECT * FROM canvas_notes WHERE id = ?", (note_id,)
        ).fetchone()
        return self._row_to_note(row) if row else None

    async def list_for_notebook(self, notebook_id: str) -> List[Dict]:
        """List all notes for a notebook, newest first."""
        rows = self._get_db().execute(
            "SELECT * FROM canvas_notes WHERE notebook_id = ? ORDER BY updated_at DESC",
            (notebook_id,),
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    async def list_all(self) -> List[Dict]:
        """List every canvas note across all notebooks, newest first."""
        rows = self._get_db().execute(
            "SELECT * FROM canvas_notes ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    async def update(
        self,
        note_id: str,
        *,
        title: Optional[str] = None,
        content_markdown: Optional[str] = None,
        content_blocknote_json: Optional[str] = None,
        notebook_id: Optional[str] = None,
        source_type: Optional[str] = None,
        note_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        voice_weight: Optional[float] = None,
        saved_as_source_id: Optional[str] = None,
        wikilinks_out: Optional[List[str]] = None,
        original_image_paths: Optional[List[str]] = None,
    ) -> Optional[Dict]:
        """Partial update — only supplied fields are changed."""
        now = datetime.utcnow().isoformat()
        sets: List[str] = ["updated_at = ?"]
        params: List = [now]

        if title is not None:
            sets.append("title = ?"); params.append(title)
        if content_markdown is not None:
            sets.append("content_markdown = ?"); params.append(content_markdown)
        if content_blocknote_json is not None:
            sets.append("content_blocknote_json = ?"); params.append(content_blocknote_json)
        if notebook_id is not None:
            sets.append("notebook_id = ?"); params.append(notebook_id)
        if source_type is not None:
            sets.append("source_type = ?"); params.append(source_type)
        if note_type is not None:
            sets.append("note_type = ?"); params.append(note_type)
        if tags is not None:
            sets.append("tags = ?"); params.append(json.dumps(tags))
        if voice_weight is not None:
            sets.append("voice_weight = ?"); params.append(voice_weight)
        if saved_as_source_id is not None:
            sets.append("saved_as_source_id = ?"); params.append(saved_as_source_id)
        if wikilinks_out is not None:
            sets.append("wikilinks_out = ?"); params.append(json.dumps(wikilinks_out))
        if original_image_paths is not None:
            sets.append("original_image_paths = ?"); params.append(json.dumps(original_image_paths))

        params.append(note_id)
        conn = self._get_db()
        conn.execute(
            f"UPDATE canvas_notes SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()

        # Record voice observation on meaningful content updates (non-fatal)
        if content_markdown is not None:
            note = await self.get(note_id)
            if note:
                self._maybe_record_observation(
                    text=content_markdown,
                    source_type=note.get('source_type', 'typed'),
                    weight=note.get('voice_weight', 1.0),
                    notebook_id=note.get('notebook_id'),
                    note_id=note_id,
                )

        return await self.get(note_id)

    async def delete(self, note_id: str) -> bool:
        """Delete a note. Returns True if a row was deleted."""
        conn = self._get_db()
        cursor = conn.execute("DELETE FROM canvas_notes WHERE id = ?", (note_id,))
        conn.commit()
        return cursor.rowcount > 0

    async def delete_all_for_notebook(self, notebook_id: str) -> int:
        """Delete all notes for a notebook. Returns count deleted."""
        conn = self._get_db()
        cursor = conn.execute(
            "DELETE FROM canvas_notes WHERE notebook_id = ?", (notebook_id,)
        )
        conn.commit()
        return cursor.rowcount

    # =========================================================================
    # Voice observation helper (Sprint 4 readiness — non-fatal)
    # =========================================================================

    def _maybe_record_observation(
        self,
        *,
        text: str,
        source_type: str,
        weight: float,
        notebook_id: Optional[str],
        note_id: Optional[str],
    ) -> None:
        """Write a voice_observation row for any text sample worth recording.

        Silently skips if the text is too short or the table doesn't exist yet.
        """
        try:
            if not text or not text.strip():
                return
            word_count = len(text.split())
            if word_count < 10:
                return  # Too short to be a meaningful voice signal
            now = datetime.utcnow().isoformat()
            conn = self._get_db()
            conn.execute(
                """
                INSERT INTO voice_observations
                    (text_sample, source_type, voice_weight, word_count,
                     notebook_id, source_note_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (text.strip(), source_type, weight, word_count,
                 notebook_id, note_id, now),
            )
            conn.commit()
        except Exception as err:
            logger.debug(f"[note-store] Voice observation skipped (non-fatal): {err}")


note_store = NoteStore()
