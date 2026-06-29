"""BriefingMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class BriefingMixin:
    def get_brief_context(self) -> str:
        """
        Pre-formatted context block for morning brief injection.

        Returns empty string if no digests exist, so brief falls back
        to today's stat-only behavior automatically.
        """
        try:
            from services.voice_engine import voice_engine
            voice_profile = voice_engine.get_profile() or {}
            interests = voice_profile.get("interests", [])

            digests = self.get_all_digests()
            connections = self.get_active_connections()

            if not digests:
                return ""

            for d in digests:
                summary = d.get("current_summary", "")
                if summary:
                    themes = json.loads(d.get("key_themes", "[]"))
                    # Boost prioritization based on voice profile interests
                    score = 0
                    if interests:
                        for interest in interests:
                            if interest.lower() in summary.lower() or any(interest.lower() in t.lower() for t in themes):
                                score += 1
                    
                    theme_note = f" (themes: {', '.join(themes[:3])})" if themes else ""
                    parts.append({
                        "text": f"**{d['name']}**{theme_note}: {summary}",
                        "score": score
                    })

            if not parts:
                return ""

            # Sort by score (observed interests first)
            parts.sort(key=lambda x: x["score"], reverse=True)
            context = "\n\n".join(p["text"] for p in parts)

            if connections:
                context += "\n\n**Cross-notebook connections I've identified:**\n"
                for c in connections[:5]:
                    context += f"- {c['description']}\n"

            # Include unsurfaced reflections (Phase 4C: auto-mark surfaced on inclusion)
            unsurfaced = self.get_unsurfaced_reflections(limit=2)
            if unsurfaced:
                context += "\n\n**Recent observations:**\n"
                ids_to_mark = []
                for r in unsurfaced:
                    context += f"- {r['content']}\n"
                    ids_to_mark.append(r["id"])
                # Mark as surfaced so they don't repeat in the next brief
                self.mark_reflections_surfaced(ids_to_mark)

            return context

        except Exception as e:
            logger.debug(f"[CuratorBrain] get_brief_context failed (non-fatal): {e}")
            return ""

    def compute_understanding_diff(
        self,
        notebook_id: str,
        since_iso: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return what changed in the curator's understanding of this
        notebook since `since_iso`. Used by Phase 5 morning brief to
        synthesize a "What's new in your thinking" section.

        Returns shape:
          {
            "new_connections":      [...],  # connections discovered > since, strength > 0.6
            "mental_model_changes": [...],  # fields edited/re-inferred > since
            "new_dissent_sources":  [...],  # stance rows scored > since with stance=contradicts
            "stance_shifts":        [...],  # sources whose stance changed > since (best-effort
                                            # approximation — we can detect re-scoring activity,
                                            # not actual stance value changes without history)
          }

        Each list may be empty. Caller filters / formats as needed.
        """
        out: Dict[str, List[Dict[str, Any]]] = {
            "new_connections": [],
            "mental_model_changes": [],
            "new_dissent_sources": [],
            "stance_shifts": [],
        }
        try:
            # New high-confidence connections involving this notebook
            cur = self._conn.execute(
                """SELECT id, notebook_a, notebook_b, description, strength, discovered_at
                   FROM connections
                   WHERE (notebook_a = ? OR notebook_b = ?)
                     AND status = 'active'
                     AND strength > 0.6
                     AND discovered_at > ?
                   ORDER BY discovered_at DESC LIMIT 10""",
                (notebook_id, notebook_id, since_iso),
            )
            for r in cur.fetchall():
                out["new_connections"].append({
                    "id": r["id"],
                    "description": r["description"],
                    "strength": r["strength"],
                    "discovered_at": r["discovered_at"],
                })

            # Mental model field changes — last_user_edit_at OR last_inferred_at > since
            row = self._conn.execute(
                """SELECT thesis, goals, audience, stage, blocked_on, recent_focus,
                          last_inferred_at, last_user_edit_at
                   FROM mental_models WHERE notebook_id = ?""",
                (notebook_id,),
            ).fetchone()
            if row:
                last_inf = row["last_inferred_at"] or ""
                last_edit = row["last_user_edit_at"] or ""
                if last_inf > since_iso or last_edit > since_iso:
                    out["mental_model_changes"].append({
                        "thesis": row["thesis"] or "",
                        "stage": row["stage"] or "",
                        "blocked_on": row["blocked_on"] or "",
                        "last_inferred_at": last_inf or None,
                        "last_user_edit_at": last_edit or None,
                    })

            # New dissent sources (stance = contradicts, scored > since)
            cur = self._conn.execute(
                """SELECT source_id, stance, confidence, rationale, scored_at
                   FROM source_stances
                   WHERE notebook_id = ?
                     AND stance = 'contradicts'
                     AND scored_at > ?
                   ORDER BY confidence DESC, scored_at DESC LIMIT 5""",
                (notebook_id, since_iso),
            )
            for r in cur.fetchall():
                out["new_dissent_sources"].append({
                    "source_id": r["source_id"],
                    "stance": r["stance"],
                    "confidence": r["confidence"],
                    "rationale": r["rationale"] or "",
                    "scored_at": r["scored_at"],
                })

            # Stance shifts — sources where scored_at > since.
            # We can't easily detect "stance changed" without history,
            # but re-scoring activity is a reasonable proxy.
            cur = self._conn.execute(
                """SELECT source_id, stance, confidence, scored_at
                   FROM source_stances
                   WHERE notebook_id = ?
                     AND scored_at > ?
                     AND stance != 'contradicts'
                   ORDER BY scored_at DESC LIMIT 10""",
                (notebook_id, since_iso),
            )
            for r in cur.fetchall():
                out["stance_shifts"].append({
                    "source_id": r["source_id"],
                    "stance": r["stance"],
                    "confidence": r["confidence"],
                    "scored_at": r["scored_at"],
                })

            return out
        except Exception as e:
            logger.warning(f"[CuratorBrain] compute_understanding_diff failed: {e}")
            return out

    def compute_brief_observations(
        self,
        notebook_id: str,
        since_iso: str,
    ) -> Dict[str, Any]:
        """Return structured observations for the morning-brief synthesizer
        to lead with (Curator Phase 6a — 2026-05-13).

        The synthesizer is instructed: "lead with these observations,
        then the per-notebook context, then stats." This is the data
        source for the "smart colleague who noticed" signal.

        Shape (all fields nullable — caller decides what to surface):
          {
            "blocked_on":              str | None,         # current friction
            "recent_focus":            str | None,         # where user has zoomed in
            "stage":                   str | None,         # exploration/synth/...
            "dissent_count":           int,                # contradicting sources
            "fresh_dissent_rationale": str | None,         # top recent one
            "is_quiet":                bool,               # no engagement in 7d
            "recent_completed_plans":  list[str],          # plan summaries
            "new_connections":         list[str],          # connection descriptions
            "fresh_reclassifications": int,                # stance shifts since
            "has_pending_draft":       bool,               # anticipatory draft ready
            "pending_draft_kind":      str | None,
          }
        """
        out: Dict[str, Any] = {
            "blocked_on": None,
            "recent_focus": None,
            "stage": None,
            "dissent_count": 0,
            "fresh_dissent_rationale": None,
            "is_quiet": False,
            "recent_completed_plans": [],
            "new_connections": [],
            "fresh_reclassifications": 0,
            "has_pending_draft": False,
            "pending_draft_kind": None,
        }
        try:
            from datetime import timedelta

            # Mental model fields
            mm = self.get_mental_model(notebook_id)
            if mm:
                out["blocked_on"] = (mm.get("blocked_on") or "").strip() or None
                out["recent_focus"] = (mm.get("recent_focus") or "").strip() or None
                out["stage"] = (mm.get("stage") or "").strip() or None

            # Dissent summary
            counts = self.get_notebook_stance_counts(notebook_id)
            out["dissent_count"] = counts.get("contradicts", 0)
            dissenters = self.get_dissenting_sources(notebook_id, limit=1)
            if dissenters:
                out["fresh_dissent_rationale"] = dissenters[0].get("rationale") or None

            # Quiet detection — no engagement_events in 7 days
            quiet_cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            recent_eng = self._conn.execute(
                "SELECT 1 FROM engagement_events WHERE notebook_id = ? AND ts > ? LIMIT 1",
                (notebook_id, quiet_cutoff),
            ).fetchone()
            out["is_quiet"] = recent_eng is None

            # Recent plan completions (last 24h)
            day_ago = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            cur = self._conn.execute(
                """SELECT summary FROM plans
                   WHERE notebook_id = ?
                     AND status = 'completed'
                     AND finished_at > ?
                     AND user_visible = 1
                   ORDER BY finished_at DESC LIMIT 3""",
                (notebook_id, day_ago),
            )
            out["recent_completed_plans"] = [r["summary"] for r in cur.fetchall() if r["summary"]]

            # New cross-notebook connections (since brief)
            cur = self._conn.execute(
                """SELECT description FROM connections
                   WHERE (notebook_a = ? OR notebook_b = ?)
                     AND status = 'active'
                     AND strength > 0.6
                     AND discovered_at > ?
                   ORDER BY discovered_at DESC LIMIT 3""",
                (notebook_id, notebook_id, since_iso),
            )
            out["new_connections"] = [r["description"] for r in cur.fetchall()]

            # Fresh stance reclassifications (count of stances re-scored since)
            row = self._conn.execute(
                """SELECT COUNT(*) AS n FROM source_stances
                   WHERE notebook_id = ? AND scored_at > ?""",
                (notebook_id, since_iso),
            ).fetchone()
            out["fresh_reclassifications"] = row["n"] if row else 0

            # Pending anticipatory draft
            draft = self.get_latest_unconsumed_draft(notebook_id)
            if draft:
                out["has_pending_draft"] = True
                out["pending_draft_kind"] = draft.get("kind")

            return out
        except Exception as e:
            logger.warning(f"[CuratorBrain] compute_brief_observations failed: {e}")
            return out

    def get_weakest_hypothesis(
        self,
        notebook_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the lowest-confidence active claim the curator is holding.

        Curator Phase 4 (2026-05-13). Scans across mental_models (whole-
        model confidence), connections (strength), and insights
        (confidence). Returns the single weakest item, or None if no
        active claims exist.

        When notebook_id is given, scopes mental_models to that notebook;
        connections + insights are inherently cross-notebook so they're
        always considered.

        Return shape:
          {
            "kind": "mental_model" | "connection" | "insight",
            "subject_id": str,
            "content": str,
            "confidence": float,
            "notebook_id": str | None,
            "source_table": str,
          }
        """
        try:
            candidates: List[Dict[str, Any]] = []

            # Mental model — weakest single notebook (or scoped notebook)
            mm_sql = """SELECT notebook_id, thesis, stage, confidence
                       FROM mental_models
                       WHERE confidence > 0 AND (thesis != '' OR stage != '')"""
            mm_params: List[Any] = []
            if notebook_id:
                mm_sql += " AND notebook_id = ?"
                mm_params.append(notebook_id)
            mm_sql += " ORDER BY confidence ASC LIMIT 1"
            mm_row = self._conn.execute(mm_sql, mm_params).fetchone()
            if mm_row:
                thesis = mm_row["thesis"] or "(no thesis)"
                stage = mm_row["stage"] or ""
                content = f"Thesis: \"{thesis[:200]}\""
                if stage:
                    content += f" (stage: {stage})"
                candidates.append({
                    "kind": "mental_model",
                    "subject_id": mm_row["notebook_id"],
                    "content": content,
                    "confidence": mm_row["confidence"],
                    "notebook_id": mm_row["notebook_id"],
                    "source_table": "mental_models",
                })

            # Connections — weakest active
            c_row = self._conn.execute(
                """SELECT id, notebook_a, notebook_b, description, strength
                   FROM connections
                   WHERE status = 'active' AND strength > 0
                   ORDER BY strength ASC LIMIT 1"""
            ).fetchone()
            if c_row:
                candidates.append({
                    "kind": "connection",
                    "subject_id": str(c_row["id"]),
                    "content": c_row["description"] or "",
                    "confidence": c_row["strength"],
                    "notebook_id": c_row["notebook_a"],
                    "source_table": "connections",
                })

            # Insights — weakest non-dismissed
            i_row = self._conn.execute(
                """SELECT id, summary, confidence, entity, notebooks
                   FROM insights
                   WHERE dismissed = 0 AND confidence > 0
                   ORDER BY confidence ASC LIMIT 1"""
            ).fetchone()
            if i_row:
                # Pick first notebook from the JSON list if present
                try:
                    nb_list = json.loads(i_row["notebooks"]) if i_row["notebooks"] else []
                except (json.JSONDecodeError, TypeError):
                    nb_list = []
                candidates.append({
                    "kind": "insight",
                    "subject_id": str(i_row["id"]),
                    "content": i_row["summary"] or "",
                    "confidence": i_row["confidence"],
                    "notebook_id": nb_list[0] if nb_list else None,
                    "source_table": "insights",
                })

            if not candidates:
                return None

            # Return the absolute weakest
            candidates.sort(key=lambda x: x["confidence"])
            return candidates[0]
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_weakest_hypothesis failed: {e}")
            return None
