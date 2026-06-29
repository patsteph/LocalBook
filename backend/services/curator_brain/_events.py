"""EventsMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class EventsMixin:
    def record_event(
        self,
        ts: str,
        actor: str,
        action: str,
        notebook_id: Optional[str] = None,
        intent: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        outcome: Optional[str] = None,
    ) -> Optional[int]:
        """Persist a single event row. Returns the row id or None on failure."""
        try:
            cur = self._conn.execute(
                """INSERT INTO events
                   (ts, notebook_id, actor, action, intent, payload, outcome)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    notebook_id,
                    actor,
                    action,
                    intent,
                    json.dumps(payload or {}),
                    outcome,
                ),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.warning(f"[CuratorBrain] record_event failed: {e}")
            return None

    def recent_events(
        self,
        limit: int = 100,
        actor: Optional[str] = None,
        notebook_id: Optional[str] = None,
        since_iso: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return recent events newest-first, optionally filtered."""
        try:
            clauses = []
            params: List[Any] = []
            if actor:
                clauses.append("actor = ?")
                params.append(actor)
            if notebook_id:
                clauses.append("notebook_id = ?")
                params.append(notebook_id)
            if since_iso:
                clauses.append("ts > ?")
                params.append(since_iso)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur = self._conn.execute(
                f"""SELECT id, ts, notebook_id, actor, action, intent, payload, outcome
                    FROM events{where}
                    ORDER BY ts DESC
                    LIMIT ?""",
                params,
            )
            rows = []
            for row in cur.fetchall():
                try:
                    payload = json.loads(row["payload"]) if row["payload"] else {}
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                rows.append({
                    "id": row["id"],
                    "ts": row["ts"],
                    "notebook_id": row["notebook_id"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "intent": row["intent"],
                    "payload": payload,
                    "outcome": row["outcome"],
                })
            return rows
        except Exception as e:
            logger.warning(f"[CuratorBrain] recent_events failed: {e}")
            return []
