"""ReflectionsMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class ReflectionsMixin:
    def add_reflection(
        self,
        content: str,
        evidence_notebooks: Optional[List[str]] = None,
        evidence_concepts: Optional[List[str]] = None,
        importance: int = 3,
    ) -> Optional[int]:
        """
        Store a Curator-generated reflection about the user's research.

        Called by:
          - memory_manager Tier 2 cross-notebook scan (Phase 3C)
          - maybe_generate_reflection (Phase 4A)

        Returns the new reflection ID, or None on failure.
        """
        try:
            cursor = self._conn.execute(
                """INSERT INTO reflections
                   (content, evidence_notebooks, evidence_concepts, importance, created_at, surfaced)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (
                    content,
                    json.dumps(evidence_notebooks or []),
                    json.dumps(evidence_concepts or []),
                    importance,
                    datetime.utcnow().isoformat(),
                )
            )
            self._conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.warning(f"[CuratorBrain] add_reflection failed (non-fatal): {e}")
            return None

    def get_unsurfaced_reflections(self, limit: int = 3) -> List[Dict]:
        """Get reflections not yet shown to the user, most important first."""
        try:
            rows = self._conn.execute(
                """SELECT * FROM reflections
                   WHERE surfaced = 0
                   ORDER BY importance DESC, created_at DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_unsurfaced_reflections failed: {e}")
            return []

    def mark_reflections_surfaced(self, reflection_ids: List[int]) -> None:
        """Mark reflections as shown to the user."""
        if not reflection_ids:
            return
        try:
            placeholders = ",".join("?" * len(reflection_ids))
            self._conn.execute(
                f"UPDATE reflections SET surfaced = 1 WHERE id IN ({placeholders})",
                reflection_ids
            )
            self._conn.commit()
        except Exception as e:
            logger.debug(f"[CuratorBrain] mark_reflections_surfaced failed: {e}")

    def add_insights(self, insights: List[Dict[str, Any]]) -> int:
        """Replace ALL active insights with a fresh batch.

        Matches the legacy behaviour where discover_cross_notebook_patterns
        rebuilds the pending list every run. Dismissed/thumbs_up insights
        are preserved (they may resurface but the user's signal is kept).

        Args:
            insights: list of dicts with keys insight_type, entity (opt),
                      notebooks (list), summary, confidence.

        Returns: count of insights persisted.
        """
        if not insights:
            return 0
        try:
            now = datetime.utcnow().isoformat()
            # Soft-delete: mark non-dismissed/non-thumbs-up insights as
            # superseded by setting dismissed=1. Preserves user signal.
            self._conn.execute(
                "UPDATE insights SET dismissed = 1 WHERE dismissed = 0 AND thumbs_up = 0"
            )
            rows = [
                (
                    ins.get("insight_type", "cross_reference"),
                    ins.get("entity"),
                    json.dumps(ins.get("notebooks", [])),
                    ins.get("summary", ""),
                    float(ins.get("confidence", 0.5)),
                    now,
                )
                for ins in insights
            ]
            self._conn.executemany(
                """INSERT INTO insights
                   (insight_type, entity, notebooks, summary, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self._conn.commit()
            return len(rows)
        except Exception as e:
            logger.warning(f"[CuratorBrain] add_insights failed: {e}")
            return 0

    def get_active_insights(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return all non-dismissed insights, newest first."""
        try:
            cur = self._conn.execute(
                """SELECT id, insight_type, entity, notebooks, summary, confidence,
                          created_at, surfaced_count, last_surfaced, thumbs_up
                   FROM insights
                   WHERE dismissed = 0
                   ORDER BY thumbs_up DESC, created_at DESC
                   LIMIT ?""",
                (limit,),
            )
            return [self._row_to_insight_dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_active_insights failed: {e}")
            return []

    def find_insights_by_entity(self, entity_query: str) -> List[Dict[str, Any]]:
        """Find active insights whose entity appears in entity_query (case-insensitive).

        Used by curator.surface_insight_if_relevant to detect when the
        user's message mentions an entity the curator has noted.
        """
        try:
            cur = self._conn.execute(
                """SELECT id, insight_type, entity, notebooks, summary, confidence,
                          created_at, surfaced_count, last_surfaced, thumbs_up
                   FROM insights
                   WHERE dismissed = 0 AND entity IS NOT NULL AND entity != ''"""
            )
            query_lower = entity_query.lower()
            hits = []
            for row in cur.fetchall():
                if row["entity"].lower() in query_lower:
                    hits.append(self._row_to_insight_dict(row))
            return hits
        except Exception as e:
            logger.warning(f"[CuratorBrain] find_insights_by_entity failed: {e}")
            return []

    def mark_insight_surfaced(self, insight_id: int) -> None:
        try:
            self._conn.execute(
                """UPDATE insights
                   SET surfaced_count = surfaced_count + 1,
                       last_surfaced = ?
                   WHERE id = ?""",
                (datetime.utcnow().isoformat(), insight_id),
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"[CuratorBrain] mark_insight_surfaced failed: {e}")

    def dismiss_insight(self, insight_id: int) -> bool:
        try:
            self._conn.execute(
                "UPDATE insights SET dismissed = 1 WHERE id = ?", (insight_id,)
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] dismiss_insight failed: {e}")
            return False

    def thumbs_up_insight(self, insight_id: int) -> bool:
        try:
            self._conn.execute(
                "UPDATE insights SET thumbs_up = 1 WHERE id = ?", (insight_id,)
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] thumbs_up_insight failed: {e}")
            return False

    @staticmethod
    def _row_to_insight_dict(row) -> Dict[str, Any]:
        """Normalize a SQLite row into the shape callers expect."""
        try:
            notebooks = json.loads(row["notebooks"]) if row["notebooks"] else []
        except (json.JSONDecodeError, TypeError):
            notebooks = []
        return {
            "id": row["id"],
            "insight_type": row["insight_type"],
            "entity": row["entity"],
            "notebooks": notebooks,
            "summary": row["summary"],
            "confidence": row["confidence"],
            "created_at": row["created_at"],
            "surfaced_count": row["surfaced_count"],
            "last_surfaced": row["last_surfaced"],
            "thumbs_up": bool(row["thumbs_up"]),
        }

    async def maybe_generate_reflection(self) -> Optional[str]:
        """
        Generate a reflection if enough has happened and we haven't over-generated.
        Max 3 reflections per day.
        """
        try:
            from datetime import timedelta
            cutoff = (datetime.utcnow() - timedelta(days=1)).isoformat()
            recent_count = self._conn.execute(
                "SELECT COUNT(*) FROM reflections WHERE created_at > ?", (cutoff,)
            ).fetchone()[0]

            if recent_count >= 3:
                return None

            digests = self.get_all_digests()
            connections = self.get_active_connections()

            if not digests:
                return None

            digest_text = "\n".join(
                f"- {d['name']}: {d['current_summary']}"
                for d in digests if d.get("current_summary")
            )
            if not digest_text:
                return None

            conn_text = (
                "\n".join(f"- {c['description']}" for c in connections[:5])
                if connections else "None identified yet."
            )

            from services.voice_engine import voice_engine
            voice_profile = voice_engine.get_profile() or {}
            interests = voice_profile.get("interests", [])
            style = voice_profile.get("style_markers", "balanced")

            prompt = (
                f"You are reviewing the user's research landscape.\n\n"
                f"User Profile & Interests:\n"
                f"- Primary Focus Areas: {', '.join(interests) if interests else 'Not yet determined'}\n"
                f"- Observed Writing Style: {style}\n\n"
                f"Current notebooks:\n{digest_text}\n\n"
                f"Cross-notebook connections:\n{conn_text}\n\n"
                f"Based on this overview, make ONE observation the user might find "
                f"interesting or useful. Focus on: emerging patterns, potential blind spots, "
                f"connections they might not have noticed, or questions worth exploring.\n"
                f"PRIORITIZE observations that align with the user's focus areas.\n"
                f"If nothing insightful comes to mind, say NONE.\n"
                f"Write 1-2 sentences only."
            )

            response = await ollama_service.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.5,
                timeout=20.0,
                num_predict=100,
            )
            text = response.get("response", "").strip()

            if text and "NONE" not in text.upper() and 15 < len(text) < 400:
                self.add_reflection(
                    content=text,
                    evidence_notebooks=[d["notebook_id"] for d in digests],
                    importance=3,
                )
                return text

            return None

        except Exception as e:
            logger.debug(f"[CuratorBrain] maybe_generate_reflection failed (non-fatal): {e}")
            return None
