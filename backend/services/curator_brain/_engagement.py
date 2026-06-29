"""EngagementMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class EngagementMixin:
    def record_engagement(
        self,
        kind: str,
        signal: str,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        notebook_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Append a row to engagement_events. Returns id or None.

        No-op when settings.engagement_tracking_enabled is False —
        returns None, doesn't touch the table.
        """
        try:
            from config import settings as _settings
            if not getattr(_settings, "engagement_tracking_enabled", True):
                return None
        except Exception:
            pass

        try:
            cur = self._conn.execute(
                """INSERT INTO engagement_events
                   (ts, notebook_id, kind, subject_type, subject_id, signal, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.utcnow().isoformat(),
                    notebook_id,
                    kind,
                    subject_type,
                    subject_id,
                    signal,
                    json.dumps(payload or {}),
                ),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.warning(f"[CuratorBrain] record_engagement failed: {e}")
            return None

    def recent_engagement(
        self,
        limit: int = 100,
        kind: Optional[str] = None,
        signal: Optional[str] = None,
        notebook_id: Optional[str] = None,
        since_iso: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return engagement events newest-first, optionally filtered."""
        try:
            clauses: List[str] = []
            params: List[Any] = []
            if kind:
                clauses.append("kind = ?")
                params.append(kind)
            if signal:
                clauses.append("signal = ?")
                params.append(signal)
            if notebook_id:
                clauses.append("notebook_id = ?")
                params.append(notebook_id)
            if since_iso:
                clauses.append("ts > ?")
                params.append(since_iso)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur = self._conn.execute(
                f"""SELECT id, ts, notebook_id, kind, subject_type, subject_id,
                           signal, payload
                    FROM engagement_events{where}
                    ORDER BY ts DESC
                    LIMIT ?""",
                params,
            )
            out: List[Dict[str, Any]] = []
            for row in cur.fetchall():
                try:
                    payload = json.loads(row["payload"]) if row["payload"] else {}
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                out.append({
                    "id": row["id"],
                    "ts": row["ts"],
                    "notebook_id": row["notebook_id"],
                    "kind": row["kind"],
                    "subject_type": row["subject_type"],
                    "subject_id": row["subject_id"],
                    "signal": row["signal"],
                    "payload": payload,
                })
            return out
        except Exception as e:
            logger.warning(f"[CuratorBrain] recent_engagement failed: {e}")
            return []

    def get_topic_engagement_summary(
        self,
        notebook_id: str,
        lookback_days: int = 30,
        limit: int = 5,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Phase 5 helper (2026-05-23 expansion): returns per-notebook
        topic engagement broken into 'liked' and 'ignored' lists. The brief
        synthesizer reads this so the LLM can write things like "I noticed
        you keep coming back to X" or "I'll surface less about Y for now."

        Returns:
            {
              "liked":   [{topic, offered, clicked, ratio}, ...],   # ratio > 0.4
              "ignored": [{topic, offered, clicked, ratio}, ...],   # offered>=3, clicked==0
              "lookback_days": int,
            }
        """
        try:
            since = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
            rows = self._conn.execute(
                """SELECT subject_id as topic,
                          SUM(CASE WHEN signal IN ('clicked','story_clicked','thumbs_up') THEN 1 ELSE 0 END) as positive,
                          SUM(CASE WHEN signal='offered' THEN 1 ELSE 0 END) as offered,
                          SUM(CASE WHEN signal='thumbs_down' THEN 1 ELSE 0 END) as down
                   FROM engagement_events
                   WHERE notebook_id = ?
                     AND ts > ?
                     AND kind = 'brief'
                     AND subject_type = 'topic'
                   GROUP BY subject_id""",
                (notebook_id, since),
            ).fetchall()
            liked: List[Dict[str, Any]] = []
            ignored: List[Dict[str, Any]] = []
            for r in rows:
                topic = r["topic"] or ""
                positive = r["positive"] or 0
                offered = r["offered"] or 0
                down = r["down"] or 0
                if not topic:
                    continue
                ratio = positive / max(offered, 1)
                if positive > 0 and ratio > 0.4:
                    liked.append({"topic": topic, "offered": offered, "clicked": positive, "ratio": round(ratio, 2)})
                elif offered >= 3 and positive == 0:
                    ignored.append({"topic": topic, "offered": offered, "clicked": 0, "ratio": 0.0, "thumbs_down": down})
            liked.sort(key=lambda x: -x["ratio"])
            ignored.sort(key=lambda x: -x["offered"])
            return {
                "liked": liked[:limit],
                "ignored": ignored[:limit],
                "lookback_days": lookback_days,
            }
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_topic_engagement_summary failed: {e}")
            return {"liked": [], "ignored": [], "lookback_days": lookback_days}

    def is_topic_repeatedly_ignored(
        self,
        notebook_id: str,
        topic_key: str,
        lookback_days: int = 14,
        threshold: int = 3,
    ) -> bool:
        """Phase 5 (2026-05-23): true when a topic has been offered ≥threshold
        times in lookback_days with ZERO positive engagement. Story ranker
        uses this to demote repeatedly-ignored topics rather than just not
        boosting them (anti-stale signal — was the missing other half of
        the engagement-aware brief)."""
        key = (topic_key or "").strip().lower()
        if not key:
            return False
        try:
            since = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
            row = self._conn.execute(
                """SELECT
                     SUM(CASE WHEN signal IN ('clicked','story_clicked','thumbs_up') THEN 1 ELSE 0 END) as positive,
                     SUM(CASE WHEN signal='offered' THEN 1 ELSE 0 END) as offered
                   FROM engagement_events
                   WHERE notebook_id = ?
                     AND ts > ?
                     AND LOWER(COALESCE(subject_id, '')) LIKE ?""",
                (notebook_id, since, f"%{key}%"),
            ).fetchone()
            if not row:
                return False
            offered = row["offered"] or 0
            positive = row["positive"] or 0
            return offered >= threshold and positive == 0
        except Exception as e:
            logger.debug(f"[CuratorBrain] is_topic_repeatedly_ignored failed: {e}")
            return False

    def get_topic_click_score(
        self,
        notebook_id: str,
        topic_key: str,
        lookback_days: int = 30,
    ) -> float:
        """Return engagement ratio for a topic over a rolling window.

        Curator Phase 5 (2026-05-13). Used by morning brief to boost
        stories about topics the user has historically clicked.

        Ratio = (clicked + thumbs_up) / max(offered, 1) over the window.
        Returns 0 for new topics with no offer history. Range typically
        [0, 1] — can exceed 1 if a story was clicked + thumbs_up'd
        without an explicit "offered" event captured first.
        """
        key = (topic_key or "").strip().lower()
        if not key:
            return 0.0
        try:
            from datetime import timedelta
            since = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
            row = self._conn.execute(
                """SELECT
                     SUM(CASE WHEN signal IN ('clicked', 'story_clicked', 'thumbs_up')
                              THEN 1 ELSE 0 END) AS positive,
                     SUM(CASE WHEN signal = 'offered' THEN 1 ELSE 0 END) AS offered
                   FROM engagement_events
                   WHERE notebook_id = ?
                     AND ts > ?
                     AND LOWER(COALESCE(subject_id, '')) LIKE ?""",
                (notebook_id, since, f"%{key}%"),
            ).fetchone()
            if not row:
                return 0.0
            offered = row["offered"] or 0
            positive = row["positive"] or 0
            if offered == 0 and positive == 0:
                return 0.0
            return positive / max(offered, 1)
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_topic_click_score failed: {e}")
            return 0.0

    def engagement_stats_by_topic(
        self,
        notebook_id: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Dict[str, int]]:
        """Aggregate engagement by subject_id over a rolling window.

        Returns {subject_id: {signal: count, ...}, ...}. Used by Phase 5
        smart brief to weight topic selection ("user clicks AI safety
        stories, ignores crypto stories").
        """
        try:
            from datetime import timedelta
            since = (datetime.utcnow() - timedelta(days=days)).isoformat()
            clauses = ["ts > ?", "subject_id IS NOT NULL"]
            params: List[Any] = [since]
            if notebook_id:
                clauses.append("notebook_id = ?")
                params.append(notebook_id)
            where = " WHERE " + " AND ".join(clauses)
            cur = self._conn.execute(
                f"""SELECT subject_id, signal, COUNT(*) AS n
                    FROM engagement_events{where}
                    GROUP BY subject_id, signal""",
                params,
            )
            out: Dict[str, Dict[str, int]] = {}
            for row in cur.fetchall():
                bucket = out.setdefault(row["subject_id"], {})
                bucket[row["signal"]] = row["n"]
            return out
        except Exception as e:
            logger.warning(f"[CuratorBrain] engagement_stats_by_topic failed: {e}")
            return {}
