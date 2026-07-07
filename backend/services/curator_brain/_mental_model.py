"""MentalModelMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403
from ._models import _stance_rescore_sf  # underscore — not via import *


class MentalModelMixin:
    _MENTAL_MODEL_FIELDS = (
        "thesis",
        "goals",
        "audience",
        "stage",
        "blocked_on",
        "recent_focus",
    )

    _MENTAL_MODEL_MIN_SOURCES = 5

    def get_mental_model(self, notebook_id: str) -> Optional[Dict[str, Any]]:
        """Return the mental model row (decoded). None if not yet created."""
        try:
            row = self._conn.execute(
                """SELECT notebook_id, thesis, goals, audience, stage,
                          blocked_on, recent_focus, pinned_fields,
                          confidence, last_inferred_at, last_user_edit_at
                   FROM mental_models WHERE notebook_id = ?""",
                (notebook_id,),
            ).fetchone()
            if not row:
                return None
            try:
                goals = json.loads(row["goals"]) if row["goals"] else []
            except (json.JSONDecodeError, TypeError):
                goals = []
            try:
                pinned = json.loads(row["pinned_fields"]) if row["pinned_fields"] else []
            except (json.JSONDecodeError, TypeError):
                pinned = []
            return {
                "notebook_id": row["notebook_id"],
                "thesis": row["thesis"] or "",
                "goals": goals,
                "audience": row["audience"] or "",
                "stage": row["stage"] or "",
                "blocked_on": row["blocked_on"] or "",
                "recent_focus": row["recent_focus"] or "",
                "pinned_fields": pinned,
                "confidence": row["confidence"] if row["confidence"] is not None else 0.0,
                "last_inferred_at": row["last_inferred_at"],
                "last_user_edit_at": row["last_user_edit_at"],
            }
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_mental_model failed: {e}")
            return None

    def set_mental_model_field(
        self,
        notebook_id: str,
        field: str,
        value: Any,
        by_user: bool = False,
    ) -> bool:
        """Set a single field on the mental model. Creates the row if missing.

        When by_user=True, stamps last_user_edit_at. by_user=False is the
        inference path (does not stamp user-edit timestamp).

        `goals` accepts a list and is JSON-serialized. Other fields accept
        strings (coerced via str()).

        Curator Phase 3b: when field='thesis' and the value actually
        changes (hash mismatch), schedules a background re-score of all
        source stances in this notebook. Stances become stale when the
        thesis they were scored against changes.
        """
        if field not in self._MENTAL_MODEL_FIELDS:
            logger.warning(f"[CuratorBrain] set_mental_model_field: unknown field {field!r}")
            return False

        # For thesis specifically, capture the prior value so we can
        # detect a real change after the write.
        prior_thesis: Optional[str] = None
        if field == "thesis":
            existing = self.get_mental_model(notebook_id)
            if existing:
                prior_thesis = existing.get("thesis") or ""

        try:
            # Ensure the row exists.
            self._conn.execute(
                "INSERT OR IGNORE INTO mental_models (notebook_id) VALUES (?)",
                (notebook_id,),
            )
            stored: Any
            if field == "goals":
                if not isinstance(value, list):
                    value = [str(value)] if value else []
                stored = json.dumps(value)
            else:
                stored = str(value) if value is not None else ""

            params = [stored]
            sql = f"UPDATE mental_models SET {field} = ?"
            if by_user:
                sql += ", last_user_edit_at = ?"
                params.append(datetime.utcnow().isoformat())
            sql += " WHERE notebook_id = ?"
            params.append(notebook_id)
            self._conn.execute(sql, params)
            self._conn.commit()

            # Thesis change → background re-score (Phase 3b).
            if field == "thesis":
                new_thesis = str(value) if value is not None else ""
                if (
                    new_thesis.strip()
                    and self._thesis_hash(new_thesis) != self._thesis_hash(prior_thesis or "")
                ):
                    try:
                        _stance_rescore_sf.spawn(
                            notebook_id,
                            lambda: self.rescore_notebook_stances(notebook_id),
                        )
                        logger.info(
                            f"[CuratorBrain] thesis changed for notebook {notebook_id[:8]}; "
                            f"scheduled background re-score"
                        )
                    except RuntimeError:
                        # No running event loop (e.g. called from sync test).
                        # The HTTP API path always has one, so this is fine.
                        pass
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] set_mental_model_field failed: {e}")
            return False

    def pin_field(self, notebook_id: str, field: str) -> bool:
        """Add a field to the notebook's pinned_fields list. Idempotent."""
        if field not in self._MENTAL_MODEL_FIELDS:
            return False
        return self._mutate_pinned_set(notebook_id, field, add=True)

    def unpin_field(self, notebook_id: str, field: str) -> bool:
        """Remove a field from the notebook's pinned_fields list. Idempotent."""
        if field not in self._MENTAL_MODEL_FIELDS:
            return False
        return self._mutate_pinned_set(notebook_id, field, add=False)

    def _mutate_pinned_set(self, notebook_id: str, field: str, add: bool) -> bool:
        try:
            model = self.get_mental_model(notebook_id) or {"pinned_fields": []}
            pinned = set(model.get("pinned_fields") or [])
            if add:
                pinned.add(field)
            else:
                pinned.discard(field)
            # Ensure row exists then write the new set.
            self._conn.execute(
                "INSERT OR IGNORE INTO mental_models (notebook_id) VALUES (?)",
                (notebook_id,),
            )
            self._conn.execute(
                """UPDATE mental_models
                   SET pinned_fields = ?, last_user_edit_at = ?
                   WHERE notebook_id = ?""",
                (json.dumps(sorted(pinned)), datetime.utcnow().isoformat(), notebook_id),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] _mutate_pinned_set failed: {e}")
            return False

    async def infer_mental_model(
        self,
        notebook_id: str,
        force: bool = False,
        debounce_seconds: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Run fast-model inference to produce/update the notebook's mental model.

        Reads recent sources + notebook digest, asks the fast model for
        a structured JSON output, merges with the existing model (pinned
        fields preserved), persists, returns the new model.

        Debounce: if `last_inferred_at` is within `debounce_seconds` and
        `force=False`, skips and returns the existing model unchanged.
        Returns None on hard failure (LLM unavailable, parse failed beyond
        repair). Caller is responsible for handling None gracefully.
        """
        existing = self.get_mental_model(notebook_id) or {}

        # Debounce check
        if not force and existing.get("last_inferred_at"):
            try:
                from datetime import timedelta
                last = datetime.fromisoformat(existing["last_inferred_at"])
                if (datetime.utcnow() - last) < timedelta(seconds=debounce_seconds):
                    logger.debug(
                        f"[CuratorBrain] infer_mental_model({notebook_id}): "
                        f"debounced (last inferred {last.isoformat()})"
                    )
                    return existing
            except (ValueError, TypeError):
                pass  # Bad timestamp — proceed with inference.

        # Gather context: recent sources + notebook digest.
        try:
            from storage.source_store import source_store
            sources = await source_store.list(notebook_id)
        except Exception as e:
            logger.warning(f"[CuratorBrain] infer_mental_model: source_store failed: {e}")
            sources = []

        if not sources:
            logger.debug(f"[CuratorBrain] infer_mental_model({notebook_id}): no sources, skipping")
            return existing or None

        total_sources = len(sources)

        # Stratified sample so notebooks with many sources don't get
        # inference biased to one temporal slice. Sort by created_at
        # descending (newest first), then sample:
        #   - newest 10  (recent_focus signal)
        #   - oldest 5   (project-history signal)
        #   - middle 5   (breadth signal)
        # Curator Phase 3a fix (2026-05-13): user reported a 100+ source
        # notebook had inference dominated by ~1/6 of sources. Stratified
        # sampling + explicit "this is a SAMPLE of N total" framing
        # rebalances the prompt and increases reliance on the digest.
        def _src_ts(s: Dict[str, Any]) -> str:
            return s.get("created_at") or s.get("added_at") or ""

        sorted_sources = sorted(sources, key=_src_ts, reverse=True)
        sample: List[Dict[str, Any]] = []
        seen_ids: set = set()

        def _push(items: List[Dict[str, Any]]) -> None:
            for it in items:
                _id = it.get("id") or it.get("source_id") or it.get("url") or ""
                if _id and _id not in seen_ids:
                    seen_ids.add(_id)
                    sample.append(it)

        if total_sources <= 20:
            _push(sorted_sources)
        else:
            _push(sorted_sources[:10])                         # newest
            _push(sorted_sources[-5:])                          # oldest
            # Middle stratified — evenly spaced indices in the middle range
            middle = sorted_sources[10:-5]
            if middle:
                step = max(1, len(middle) // 5)
                _push(middle[::step][:5])

        # Pull notebook digest if available — primary signal for whole-
        # notebook understanding (thesis, audience, goals).
        digest = self.get_digest(notebook_id) or {}

        # Build context lines for the LLM.
        source_lines = []
        for s in sample[:20]:
            title = (s.get("filename") or s.get("title") or s.get("url") or "Untitled")[:120]
            kind = s.get("type") or s.get("source_type") or "source"
            source_lines.append(f"  - [{kind}] {title}")
        source_block = "\n".join(source_lines) if source_lines else "  (none)"

        digest_block = ""
        if digest.get("current_summary"):
            # Surface the digest as the primary signal — give it more room
            # and lead the prompt with it.
            digest_block = f"\nNotebook digest (this is the curator's prior whole-notebook summary; lean on this for thesis/goals/audience):\n{(digest['current_summary'] or '')[:1200]}"
        if digest.get("key_themes"):
            try:
                themes = json.loads(digest["key_themes"]) if isinstance(digest["key_themes"], str) else digest["key_themes"]
                if themes:
                    digest_block += f"\nKey themes: {', '.join(str(t) for t in themes[:8])}"
            except (json.JSONDecodeError, TypeError):
                pass

        # Surface pinned fields to the LLM so it doesn't propose values
        # the user has already committed to.
        pinned = set(existing.get("pinned_fields") or [])
        pinned_block = ""
        if pinned:
            pinned_pairs = [f"{f}: {existing.get(f)!r}" for f in pinned if existing.get(f)]
            if pinned_pairs:
                pinned_block = "\nThe user has pinned these fields — DO NOT override them, mirror their values:\n  " + "\n  ".join(pinned_pairs)

        is_sample = len(sample) < total_sources
        sample_framing = (
            f"Sources (stratified sample of {len(sample)} from {total_sources} total — "
            f"oldest, middle, and newest are all represented):"
            if is_sample
            else f"All sources in this notebook ({total_sources} total):"
        )

        prompt = (
            f"You are forming a mental model of what a researcher is working on in a notebook.\n"
            f"\n"
            f"PRIMARY SIGNAL: the notebook digest below, if present — it summarizes the "
            f"WHOLE notebook and is the best evidence for the stable picture (thesis, goals, "
            f"audience).\n"
            f"SUPPORTING SIGNAL: the source list — useful for recent_focus (newest items) "
            f"and confirming the digest. Do NOT let recent additions dominate your view of "
            f"the thesis if the digest paints a broader picture.\n"
            f"\n"
            f"Infer these fields:\n"
            f"  - thesis: a one-sentence central claim or research question\n"
            f"  - goals: a list of 1-4 concrete short-term goals\n"
            f"  - audience: who they're producing this for (one short phrase)\n"
            f"  - stage: one of 'exploration', 'gathering', 'synthesis', 'drafting', 'done'\n"
            f"  - blocked_on: anything that appears to be blocking progress, or '' if nothing\n"
            f"  - recent_focus: the topic they've zoomed in on most recently (one short phrase)\n"
            f"  - confidence: your own 0-1 confidence in this overall model\n"
            f"\n"
            f"Be specific — vague output ('research on AI') is worse than admitting low confidence.\n"
            f"\n"
            f"{sample_framing}\n{source_block}\n"
            f"{digest_block}"
            f"{pinned_block}"
            f"\n"
            f"Respond with ONLY a JSON object with exactly these keys: "
            f"thesis, goals, audience, stage, blocked_on, recent_focus, confidence. "
            f"No markdown, no commentary."
        )

        from config import settings as _settings
        try:
            from utils.json_repair import robust_json_parse
            # WS1 (2026-06-23): yield to an active foreground op (chat/visual) —
            # mental-model inference fires on source-count thresholds right after
            # an upload, exactly when the user is likely to chat. Deadlock-proof.
            from services.memory_steward import await_background_clearance
            await await_background_clearance()
            result = await ollama_service.generate(
                prompt=prompt,
                system="You output only valid JSON for mental model inference.",
                model=_settings.ollama_fast_model,
                temperature=0.3,
                timeout=45.0,
            )
            raw = (result.get("response") or "").strip()
        except Exception as e:
            logger.warning(f"[CuratorBrain] infer_mental_model LLM call failed: {e}")
            return existing or None

        parsed = robust_json_parse(raw, expect="object", fallback=None, label="MentalModelInfer")
        if not isinstance(parsed, dict):
            logger.warning(
                f"[CuratorBrain] infer_mental_model: JSON parse failed; raw={raw[:200]!r}"
            )
            return existing or None

        # Normalize values
        inferred: Dict[str, Any] = {}
        for field in self._MENTAL_MODEL_FIELDS:
            v = parsed.get(field)
            if field == "goals":
                if isinstance(v, list):
                    # Guard: skip nested dict/list elements so a "{'goal':...}" repr
                    # never lands in the goals list (2026-07-07 curator JSON-leak audit)
                    inferred[field] = [str(x).strip() for x in v if x and not isinstance(x, (dict, list))][:6]
                elif isinstance(v, str):
                    # Comma-separated fallback
                    inferred[field] = [g.strip() for g in v.split(",") if g.strip()][:6]
                else:
                    inferred[field] = []
            else:
                # Guard against str(dict)/str(list) Python-repr leaking into a
                # displayed field (thesis/audience/stage/...). (2026-07-07)
                inferred[field] = str(v).strip() if v and not isinstance(v, (dict, list)) else ""

        try:
            confidence = float(parsed.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        ok = self._upsert_inferred_mental_model(notebook_id, inferred, confidence)
        if not ok:
            return existing or None
        return self.get_mental_model(notebook_id)

    def should_trigger_inference(self, source_count: int) -> bool:
        """Return True if inference should be considered for this notebook.

        Curator Phase 3a hotfix (2026-05-13): replaced the original
        exact-match tiered policy (5, 10, 25, 50, +25) with a simple
        floor check. The exact-match policy was brittle — a user
        adding 3 sources at once could jump 4 → 7 and skip the
        threshold entirely.

        Rate limiting is already handled by infer_mental_model's 30s
        debounce, so this only needs to gate "is this notebook big
        enough to bother". The brain decides whether to actually run.
        """
        return source_count >= self._MENTAL_MODEL_MIN_SOURCES

    def _upsert_inferred_mental_model(
        self,
        notebook_id: str,
        inferred: Dict[str, Any],
        confidence: float,
    ) -> bool:
        """Internal: merge inferred values into the row, preserving pinned fields.

        Stamps last_inferred_at. Caller is responsible for the LLM call.
        """
        try:
            existing = self.get_mental_model(notebook_id) or {}
            pinned = set(existing.get("pinned_fields") or [])

            # Build the merged values for each field. Pinned fields keep
            # the existing value; non-pinned fields take the new inferred
            # value when present (otherwise keep existing).
            merged: Dict[str, Any] = {}
            for field in self._MENTAL_MODEL_FIELDS:
                if field in pinned:
                    merged[field] = existing.get(field, "" if field != "goals" else [])
                elif field in inferred and inferred[field] is not None:
                    merged[field] = inferred[field]
                else:
                    merged[field] = existing.get(field, "" if field != "goals" else [])

            # Serialize goals.
            goals_json = json.dumps(merged.get("goals") or [])

            self._conn.execute(
                "INSERT OR IGNORE INTO mental_models (notebook_id) VALUES (?)",
                (notebook_id,),
            )
            self._conn.execute(
                """UPDATE mental_models
                   SET thesis = ?, goals = ?, audience = ?, stage = ?,
                       blocked_on = ?, recent_focus = ?,
                       confidence = ?, last_inferred_at = ?
                   WHERE notebook_id = ?""",
                (
                    merged.get("thesis", ""),
                    goals_json,
                    merged.get("audience", ""),
                    merged.get("stage", ""),
                    merged.get("blocked_on", ""),
                    merged.get("recent_focus", ""),
                    float(confidence),
                    datetime.utcnow().isoformat(),
                    notebook_id,
                ),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] _upsert_inferred_mental_model failed: {e}")
            return False
