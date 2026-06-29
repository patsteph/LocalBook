"""ReputationMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class ReputationMixin:
    def get_stats(self) -> Dict[str, Any]:
        """Quick stats for debugging and /curator/brain-status endpoint."""
        try:
            digests = self._conn.execute(
                "SELECT COUNT(*) as n, SUM(CASE WHEN dirty=1 THEN 1 ELSE 0 END) as dirty FROM notebook_digests"
            ).fetchone()
            connections = self._conn.execute(
                "SELECT COUNT(*) as n FROM connections WHERE status='active'"
            ).fetchone()
            reflections = self._conn.execute(
                "SELECT COUNT(*) as n, SUM(CASE WHEN surfaced=0 THEN 1 ELSE 0 END) as unsurfaced FROM reflections"
            ).fetchone()
            return {
                "digests_total": digests["n"],
                "digests_dirty": digests["dirty"],
                "connections_active": connections["n"],
                "reflections_total": reflections["n"],
                "reflections_unsurfaced": reflections["unsurfaced"],
                "brain_db_path": str(self._db_path),
            }
        except Exception as e:
            return {"error": str(e)}

    def record_source_reputation_event(
        self,
        notebook_id: str,
        source_id: str,
        signal: str,  # 'added' | 'approved' | 'rejected'
        source_label: str = "",
    ) -> None:
        """Update the rolling reputation row for a (notebook, source).

        Phase 7.6 capture-only. Surfacing rules ("source X dropped from
        80% to 20%") read this table later; for now we just keep it
        populated so the data accumulates from day one.
        """
        try:
            now = datetime.utcnow().isoformat()
            cutoff_30d = (datetime.utcnow() - timedelta(days=30)).isoformat()
            # Upsert pattern — SQLite-friendly: try update, insert on fail.
            row = self._conn.execute(
                "SELECT id, total_events, approved_count, rejected_count, added_count, first_seen_at "
                "FROM source_reputation WHERE notebook_id=? AND source_id=?",
                (notebook_id, source_id),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """INSERT INTO source_reputation
                        (notebook_id, source_id, source_label, total_events,
                         approved_count, rejected_count, added_count,
                         first_seen_at, last_event_at)
                       VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                    (
                        notebook_id, source_id, source_label,
                        1 if signal == "approved" else 0,
                        1 if signal == "rejected" else 0,
                        1 if signal == "added" else 0,
                        now, now,
                    ),
                )
            else:
                approved = row["approved_count"] + (1 if signal == "approved" else 0)
                rejected = row["rejected_count"] + (1 if signal == "rejected" else 0)
                added = row["added_count"] + (1 if signal == "added" else 0)
                total = row["total_events"] + 1
                # Lifetime acceptance rate: both "approved" (collector queue)
                # and "added" (direct user adds — upload/extension/voice/etc.)
                # represent the user choosing to keep the source. "rejected"
                # is the only negative signal we get today. If we later add
                # an explicit "useless / remove this" signal, fold it into
                # rejected here.
                positive = approved + added
                resolved = positive + rejected
                lifetime_rate = (positive / resolved) if resolved > 0 else 0.0
                self._conn.execute(
                    """UPDATE source_reputation SET
                        total_events=?, approved_count=?, rejected_count=?,
                        added_count=?, lifetime_acceptance_rate=?, last_event_at=?
                       WHERE id=?""",
                    (total, approved, rejected, added, lifetime_rate, now, row["id"]),
                )
            # Recompute the 30d rolling window from engagement_events so it
            # stays accurate even when old rows age out. This is cheap and
            # avoids needing a per-event timestamp column on the reputation
            # table itself.
            rolling = self._conn.execute(
                """SELECT
                      SUM(CASE WHEN signal IN ('approved','added') THEN 1 ELSE 0 END) as positive,
                      SUM(CASE WHEN signal='rejected' THEN 1 ELSE 0 END) as rejected,
                      COUNT(*) as total
                   FROM engagement_events
                   WHERE notebook_id=? AND subject_id=? AND ts > ?""",
                (notebook_id, source_id, cutoff_30d),
            ).fetchone()
            r_positive = rolling["positive"] or 0
            r_rejected = rolling["rejected"] or 0
            r_total = rolling["total"] or 0
            r_resolved = r_positive + r_rejected
            rolling_rate = (r_positive / r_resolved) if r_resolved > 0 else 0.0
            self._conn.execute(
                """UPDATE source_reputation SET
                    rolling_30d_events=?, rolling_30d_approved=?,
                    rolling_30d_rejected=?, rolling_acceptance_rate=?
                   WHERE notebook_id=? AND source_id=?""",
                (r_total, r_positive, r_rejected, rolling_rate, notebook_id, source_id),
            )
            self._conn.commit()
        except Exception as e:
            logger.debug(f"[CuratorBrain] record_source_reputation_event failed: {e}")

    def get_source_reputation_summary(self, notebook_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Phase 7.6 reader. Returns reputation rows for a notebook ordered by
        rolling acceptance rate ascending — UI will surface the worst-trending
        sources first when the surfacing rule ships."""
        try:
            rows = self._conn.execute(
                """SELECT notebook_id, source_id, source_label, total_events,
                          approved_count, rejected_count, added_count,
                          rolling_30d_events, rolling_30d_approved, rolling_30d_rejected,
                          lifetime_acceptance_rate, rolling_acceptance_rate,
                          first_seen_at, last_event_at
                   FROM source_reputation
                   WHERE notebook_id=?
                   ORDER BY rolling_acceptance_rate ASC, total_events DESC
                   LIMIT ?""",
                (notebook_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_source_reputation_summary failed: {e}")
            return []

    def get_voice_scoreboard(self, lookback_days: int = 30) -> Dict[str, Any]:
        """Phase 7.2 reader. Aggregates brief engagement by voice.

        Returns:
            {
              "voices": {
                "smart_colleague": {"opens": N, "thumbs_up": M, "thumbs_down": K},
                ...
              },
              "lookback_days": int,
              "total_events": int,
            }

        UI / auto-rotation reads this. When any voice accrues ≥2 thumbs_down
        within the lookback window, that's the signal to rotate away from
        it (per Phase 7.2 compressed-v2 plan in _PHASE7_DATA_READINESS.md).
        """
        try:
            cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
            # We tagged voice in payload (see CuratorPanel useEngagement
            # calls); JSON_EXTRACT pulls it out without a separate column.
            rows = self._conn.execute(
                """SELECT
                      json_extract(payload, '$.voice') as voice,
                      signal,
                      COUNT(*) as cnt
                   FROM engagement_events
                   WHERE kind='brief'
                     AND ts > ?
                     AND json_extract(payload, '$.voice') IS NOT NULL
                   GROUP BY voice, signal""",
                (cutoff,),
            ).fetchall()
            voices: Dict[str, Dict[str, int]] = {}
            total = 0
            for r in rows:
                v = r["voice"]
                s = r["signal"]
                c = r["cnt"]
                if not v:
                    continue
                voices.setdefault(v, {"opens": 0, "thumbs_up": 0, "thumbs_down": 0})
                if s == "opened":
                    voices[v]["opens"] += c
                elif s == "thumbs_up":
                    voices[v]["thumbs_up"] += c
                elif s == "thumbs_down":
                    voices[v]["thumbs_down"] += c
                total += c
            return {
                "voices": voices,
                "lookback_days": lookback_days,
                "total_events": total,
            }
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_voice_scoreboard failed: {e}")
            return {"voices": {}, "lookback_days": lookback_days, "total_events": 0}

    def get_studio_kind_scores(self, lookback_days: int = 30) -> Dict[str, Any]:
        """Phase 7.5 reader. Aggregates Studio output engagement by skill_id.

        UI uses this to know which Studio types are landing well; learning
        layer uses it to bias anticipatory-draft medium selection.
        """
        try:
            cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
            rows = self._conn.execute(
                """SELECT
                      subject_type,
                      json_extract(payload, '$.skill_id') as skill_id,
                      signal,
                      COUNT(*) as cnt
                   FROM engagement_events
                   WHERE subject_type LIKE 'studio_%'
                     AND ts > ?
                   GROUP BY subject_type, skill_id, signal""",
                (cutoff,),
            ).fetchall()
            kinds: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                key = r["subject_type"] or "studio_unknown"
                bucket = kinds.setdefault(key, {
                    "skills": {},
                    "thumbs_up": 0,
                    "thumbs_down": 0,
                    "invoked": 0,
                })
                if r["signal"] == "thumbs_up":
                    bucket["thumbs_up"] += r["cnt"]
                elif r["signal"] == "thumbs_down":
                    bucket["thumbs_down"] += r["cnt"]
                elif r["signal"] == "invoked":
                    bucket["invoked"] += r["cnt"]
                if r["skill_id"]:
                    sk = bucket["skills"].setdefault(r["skill_id"], {
                        "invoked": 0, "thumbs_up": 0, "thumbs_down": 0,
                    })
                    if r["signal"] in ("invoked", "thumbs_up", "thumbs_down"):
                        sk[r["signal"]] += r["cnt"]
            return {"kinds": kinds, "lookback_days": lookback_days}
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_studio_kind_scores failed: {e}")
            return {"kinds": {}, "lookback_days": lookback_days}
