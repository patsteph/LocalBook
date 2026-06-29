"""GovernanceMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class GovernanceMixin:
    _NAG_VALID_RESPONSES = ("up", "down", "dismissed")

    _DRAFT_DISCARD_COOLOFF_DAYS = 14

    def record_nag(
        self,
        kind: str,
        notebook_id: Optional[str] = None,
        subject_id: Optional[str] = None,
    ) -> Optional[int]:
        """Insert a fire row. Returns the new id or None on failure."""
        try:
            cur = self._conn.execute(
                """INSERT INTO nag_log
                   (kind, notebook_id, subject_id, fired_at)
                   VALUES (?, ?, ?, ?)""",
                (kind, notebook_id, subject_id, datetime.utcnow().isoformat()),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.warning(f"[CuratorBrain] record_nag failed: {e}")
            return None

    def set_nag_response(self, nag_id: int, response: str) -> bool:
        """Stamp a user response onto a nag row. Returns True if updated."""
        if response not in self._NAG_VALID_RESPONSES:
            logger.warning(f"[CuratorBrain] set_nag_response: invalid response {response!r}")
            return False
        try:
            cur = self._conn.execute(
                "UPDATE nag_log SET user_response = ? WHERE id = ?",
                (response, nag_id),
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.warning(f"[CuratorBrain] set_nag_response failed: {e}")
            return False

    def can_fire_nag(
        self,
        kind: str,
        notebook_id: Optional[str] = None,
        cool_off_days: int = 7,
        daily_cap: int = 3,
        priority: str = "medium",
    ) -> bool:
        """Return True if a new nag of this kind is permitted right now.

        Policy:
          1. Global daily cap — at most `daily_cap` proactive fires per
             rolling 24h across all kinds. Keeps the assistant from
             feeling chatty.
          2. Per-(kind, notebook_id) cool-off — if there are ≥2
             thumbs_down in the last `cool_off_days` for the same kind
             + notebook, silence for that pair until the window clears.

        priority (Curator Phase 5 — 2026-05-13):
          - 'high'   : bypasses daily cap. Used for urgent surfaces like
                       contradiction discovery. Cool-off still applies —
                       user can shut up high-priority surfaces too.
          - 'medium' : existing logic. Default for surfaces of normal
                       importance (stagnation, ambient dissent in chat).
          - 'low'    : tighter cap (daily_cap // 2, floor 1). Used for
                       chatty/discovery surfaces like new connections.
        """
        from datetime import timedelta
        now = datetime.utcnow()

        # Apply priority adjustments to the daily cap.
        if priority == "high":
            effective_cap: Optional[int] = None  # bypass
        elif priority == "low":
            effective_cap = max(1, daily_cap // 2)
        else:
            effective_cap = daily_cap

        try:
            # Daily cap check (global, all kinds combined) — skipped for high priority.
            if effective_cap is not None:
                day_ago = (now - timedelta(hours=24)).isoformat()
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM nag_log WHERE fired_at > ?",
                    (day_ago,),
                ).fetchone()
                if row and row["n"] >= effective_cap:
                    logger.debug(
                        f"[CuratorBrain] can_fire_nag({kind}, {notebook_id}, "
                        f"priority={priority}): daily cap {effective_cap} reached "
                        f"(count={row['n']})"
                    )
                    return False

            # Per-(kind, nb) cool-off — ALWAYS applies regardless of priority.
            window = (now - timedelta(days=cool_off_days)).isoformat()
            params: List[Any] = [kind, window]
            sql = """SELECT COUNT(*) AS n FROM nag_log
                     WHERE kind = ? AND fired_at > ? AND user_response = 'down'"""
            if notebook_id:
                sql += " AND notebook_id = ?"
                params.append(notebook_id)
            row = self._conn.execute(sql, params).fetchone()
            if row and row["n"] >= 2:
                logger.debug(
                    f"[CuratorBrain] can_fire_nag({kind}, {notebook_id}, priority={priority}): "
                    f"cool-off — {row['n']} thumbs_down in last {cool_off_days}d"
                )
                return False

            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] can_fire_nag failed: {e}")
            # Fail-safe: if check fails, allow (errs on side of feature
            # working — the user can thumbs-down if it's wrong).
            return True

    def suppress_topic(
        self,
        topic_key: str,
        notebook_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Optional[int]:
        """Mute a topic keyword. Substring match against story titles.

        notebook_id None = global mute across all notebooks.
        Idempotent — re-suppressing the same key is a no-op (UNIQUE
        constraint).
        """
        key = (topic_key or "").strip().lower()
        if not key:
            return None
        try:
            cur = self._conn.execute(
                """INSERT INTO topic_suppressions
                   (notebook_id, topic_key, suppressed_at, reason)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(notebook_id, topic_key) DO UPDATE SET
                     suppressed_at = excluded.suppressed_at,
                     reason = COALESCE(excluded.reason, reason)""",
                (notebook_id, key, datetime.utcnow().isoformat(), reason),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.warning(f"[CuratorBrain] suppress_topic failed: {e}")
            return None

    def unsuppress_topic(
        self,
        topic_key: str,
        notebook_id: Optional[str] = None,
    ) -> bool:
        """Remove a topic suppression. Returns True if a row was deleted."""
        key = (topic_key or "").strip().lower()
        if not key:
            return False
        try:
            if notebook_id is None:
                cur = self._conn.execute(
                    "DELETE FROM topic_suppressions WHERE notebook_id IS NULL AND topic_key = ?",
                    (key,),
                )
            else:
                cur = self._conn.execute(
                    "DELETE FROM topic_suppressions WHERE notebook_id = ? AND topic_key = ?",
                    (notebook_id, key),
                )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.warning(f"[CuratorBrain] unsuppress_topic failed: {e}")
            return False

    def list_suppressions(self, notebook_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List active suppressions. When notebook_id given, includes its
        notebook-scoped suppressions PLUS any global ones."""
        try:
            if notebook_id is None:
                cur = self._conn.execute(
                    """SELECT id, notebook_id, topic_key, suppressed_at, reason
                       FROM topic_suppressions
                       ORDER BY suppressed_at DESC"""
                )
            else:
                cur = self._conn.execute(
                    """SELECT id, notebook_id, topic_key, suppressed_at, reason
                       FROM topic_suppressions
                       WHERE notebook_id IS NULL OR notebook_id = ?
                       ORDER BY suppressed_at DESC""",
                    (notebook_id,),
                )
            return [
                {
                    "id": r["id"],
                    "notebook_id": r["notebook_id"],
                    "topic_key": r["topic_key"],
                    "suppressed_at": r["suppressed_at"],
                    "reason": r["reason"],
                }
                for r in cur.fetchall()
            ]
        except Exception as e:
            logger.warning(f"[CuratorBrain] list_suppressions failed: {e}")
            return []

    def is_topic_suppressed(
        self,
        notebook_id: Optional[str],
        story_title: str,
    ) -> bool:
        """True if story_title contains any active suppression keyword
        for this notebook (or any global suppression).
        """
        if not story_title:
            return False
        title_lower = story_title.lower()
        try:
            rows = self.list_suppressions(notebook_id)
            for row in rows:
                key = row.get("topic_key") or ""
                if key and key in title_lower:
                    return True
            return False
        except Exception as e:
            logger.warning(f"[CuratorBrain] is_topic_suppressed failed: {e}")
            return False

    def queue_draft(
        self,
        notebook_id: str,
        kind: str,
        content_markdown: str,
        source_signal: Optional[str] = None,
    ) -> Optional[int]:
        """Persist a new anticipatory draft. Returns its id."""
        try:
            cur = self._conn.execute(
                """INSERT INTO draft_outputs
                   (notebook_id, kind, content_markdown, source_signal, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (notebook_id, kind, content_markdown, source_signal,
                 datetime.utcnow().isoformat()),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.warning(f"[CuratorBrain] queue_draft failed: {e}")
            return None

    def get_latest_unconsumed_draft(self, notebook_id: str) -> Optional[Dict[str, Any]]:
        """Return the newest non-consumed, non-discarded draft for the
        notebook. Used by @curator show draft + brief surfacing."""
        try:
            row = self._conn.execute(
                """SELECT id, notebook_id, kind, content_markdown, source_signal,
                          created_at, consumed_at, discarded_at
                   FROM draft_outputs
                   WHERE notebook_id = ?
                     AND consumed_at IS NULL
                     AND discarded_at IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (notebook_id,),
            ).fetchone()
            return self._row_to_draft_dict(row) if row else None
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_latest_unconsumed_draft failed: {e}")
            return None

    def get_latest_draft(self, notebook_id: str) -> Optional[Dict[str, Any]]:
        """Newest draft regardless of consumed/discarded state.
        Used by @curator discard draft when the user wants to discard
        a draft they already viewed.
        """
        try:
            row = self._conn.execute(
                """SELECT id, notebook_id, kind, content_markdown, source_signal,
                          created_at, consumed_at, discarded_at
                   FROM draft_outputs
                   WHERE notebook_id = ?
                     AND discarded_at IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (notebook_id,),
            ).fetchone()
            return self._row_to_draft_dict(row) if row else None
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_latest_draft failed: {e}")
            return None

    def mark_draft_consumed(self, draft_id: int) -> bool:
        try:
            self._conn.execute(
                "UPDATE draft_outputs SET consumed_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), draft_id),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] mark_draft_consumed failed: {e}")
            return False

    def mark_draft_discarded(self, draft_id: int) -> bool:
        try:
            self._conn.execute(
                "UPDATE draft_outputs SET discarded_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), draft_id),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] mark_draft_discarded failed: {e}")
            return False

    def is_drafting_suppressed(self, notebook_id: str) -> bool:
        """True if a draft for this notebook was discarded within the
        cool-off window. Caller (maybe_fire_anticipatory_draft) gates
        regeneration on this so we don't re-prep content after the user
        already said no.
        """
        try:
            from datetime import timedelta
            cutoff = (datetime.utcnow() - timedelta(days=self._DRAFT_DISCARD_COOLOFF_DAYS)).isoformat()
            row = self._conn.execute(
                """SELECT 1 FROM draft_outputs
                   WHERE notebook_id = ?
                     AND discarded_at IS NOT NULL
                     AND discarded_at > ?
                   LIMIT 1""",
                (notebook_id, cutoff),
            ).fetchone()
            return row is not None
        except Exception as e:
            logger.warning(f"[CuratorBrain] is_drafting_suppressed failed: {e}")
            return False

    @staticmethod
    def _row_to_draft_dict(row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "notebook_id": row["notebook_id"],
            "kind": row["kind"],
            "content_markdown": row["content_markdown"],
            "source_signal": row["source_signal"],
            "created_at": row["created_at"],
            "consumed_at": row["consumed_at"],
            "discarded_at": row["discarded_at"],
        }

    def queue_pending_aside(
        self,
        notebook_id: str,
        kind: str,
        aside_text: str,
        nag_id: Optional[int] = None,
    ) -> Optional[int]:
        try:
            cur = self._conn.execute(
                """INSERT INTO pending_asides
                   (notebook_id, kind, aside_text, nag_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (notebook_id, kind, aside_text, nag_id, datetime.utcnow().isoformat()),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.warning(f"[CuratorBrain] queue_pending_aside failed: {e}")
            return None

    def consume_pending_aside(self, notebook_id: str) -> Optional[Dict[str, Any]]:
        """Pop the oldest unconsumed aside for this notebook. None if empty.

        Marks consumed_at so the same aside isn't surfaced twice.
        """
        try:
            row = self._conn.execute(
                """SELECT id, notebook_id, kind, aside_text, nag_id, created_at
                   FROM pending_asides
                   WHERE notebook_id = ? AND consumed_at IS NULL
                   ORDER BY created_at ASC
                   LIMIT 1""",
                (notebook_id,),
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE pending_asides SET consumed_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), row["id"]),
            )
            self._conn.commit()
            return {
                "id": row["id"],
                "notebook_id": row["notebook_id"],
                "kind": row["kind"],
                "aside_text": row["aside_text"],
                "nag_id": row["nag_id"],
                "created_at": row["created_at"],
            }
        except Exception as e:
            logger.warning(f"[CuratorBrain] consume_pending_aside failed: {e}")
            return None
