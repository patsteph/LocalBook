"""StanceMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class StanceMixin:
    _STANCE_VALUES = ("supports", "contradicts", "tangential", "off_topic")

    @staticmethod
    def _thesis_hash(thesis: str) -> str:
        """Stable short hash of the thesis text — used to detect changes."""
        import hashlib
        return hashlib.sha256((thesis or "").strip().encode("utf-8")).hexdigest()[:16]

    def get_stance(self, source_id: str, notebook_id: str) -> Optional[Dict[str, Any]]:
        try:
            row = self._conn.execute(
                """SELECT source_id, notebook_id, stance, confidence,
                          rationale, scored_thesis_hash, scored_at
                   FROM source_stances
                   WHERE source_id = ? AND notebook_id = ?""",
                (source_id, notebook_id),
            ).fetchone()
            if not row:
                return None
            return {
                "source_id": row["source_id"],
                "notebook_id": row["notebook_id"],
                "stance": row["stance"],
                "confidence": row["confidence"],
                "rationale": row["rationale"],
                "scored_thesis_hash": row["scored_thesis_hash"],
                "scored_at": row["scored_at"],
            }
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_stance failed: {e}")
            return None

    def upsert_stance(
        self,
        source_id: str,
        notebook_id: str,
        stance: str,
        confidence: float,
        rationale: str,
        thesis_hash: str,
    ) -> bool:
        """Insert-or-update one stance row. Idempotent. Returns success."""
        if stance not in self._STANCE_VALUES:
            logger.warning(f"[CuratorBrain] upsert_stance: invalid stance {stance!r}")
            return False
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
            self._conn.execute(
                """INSERT INTO source_stances
                   (source_id, notebook_id, stance, confidence, rationale,
                    scored_thesis_hash, scored_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id, notebook_id) DO UPDATE SET
                     stance = excluded.stance,
                     confidence = excluded.confidence,
                     rationale = excluded.rationale,
                     scored_thesis_hash = excluded.scored_thesis_hash,
                     scored_at = excluded.scored_at""",
                (
                    source_id,
                    notebook_id,
                    stance,
                    confidence,
                    rationale or "",
                    thesis_hash,
                    datetime.utcnow().isoformat(),
                ),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] upsert_stance failed: {e}")
            return False

    def get_notebook_stance_counts(self, notebook_id: str) -> Dict[str, int]:
        """Return counts per stance value for a notebook. Missing buckets are 0."""
        counts = {v: 0 for v in self._STANCE_VALUES}
        try:
            cur = self._conn.execute(
                """SELECT stance, COUNT(*) AS n
                   FROM source_stances
                   WHERE notebook_id = ?
                   GROUP BY stance""",
                (notebook_id,),
            )
            for row in cur.fetchall():
                if row["stance"] in counts:
                    counts[row["stance"]] = row["n"]
            return counts
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_notebook_stance_counts failed: {e}")
            return counts

    def get_dissenting_sources(
        self,
        notebook_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Top contradicting sources for a notebook, highest confidence first."""
        try:
            cur = self._conn.execute(
                """SELECT source_id, notebook_id, stance, confidence,
                          rationale, scored_at
                   FROM source_stances
                   WHERE notebook_id = ? AND stance = 'contradicts'
                   ORDER BY confidence DESC, scored_at DESC
                   LIMIT ?""",
                (notebook_id, limit),
            )
            return [
                {
                    "source_id": r["source_id"],
                    "notebook_id": r["notebook_id"],
                    "stance": r["stance"],
                    "confidence": r["confidence"],
                    "rationale": r["rationale"] or "",
                    "scored_at": r["scored_at"],
                }
                for r in cur.fetchall()
            ]
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_dissenting_sources failed: {e}")
            return []

    def get_supporting_sources(
        self,
        notebook_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Top supporting sources for a notebook, highest confidence first.

        Mirror of `get_dissenting_sources` — used by visualizations that
        plot the stance distribution (e.g. Curator dissent overwatch
        quadrant chart).
        """
        try:
            cur = self._conn.execute(
                """SELECT source_id, notebook_id, stance, confidence,
                          rationale, scored_at
                   FROM source_stances
                   WHERE notebook_id = ? AND stance = 'supports'
                   ORDER BY confidence DESC, scored_at DESC
                   LIMIT ?""",
                (notebook_id, limit),
            )
            return [
                {
                    "source_id": r["source_id"],
                    "notebook_id": r["notebook_id"],
                    "stance": r["stance"],
                    "confidence": r["confidence"],
                    "rationale": r["rationale"] or "",
                    "scored_at": r["scored_at"],
                }
                for r in cur.fetchall()
            ]
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_supporting_sources failed: {e}")
            return []

    def clear_stances_for_notebook(self, notebook_id: str) -> int:
        """Delete all stance rows for a notebook. Returns count deleted."""
        try:
            cur = self._conn.execute(
                "DELETE FROM source_stances WHERE notebook_id = ?",
                (notebook_id,),
            )
            self._conn.commit()
            return cur.rowcount
        except Exception as e:
            logger.warning(f"[CuratorBrain] clear_stances_for_notebook failed: {e}")
            return 0

    async def score_source_stance(
        self,
        notebook_id: str,
        source_id: str,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Score one source's stance against the notebook thesis.

        Skips if no thesis exists OR thesis confidence too low OR
        existing stance's thesis_hash matches current thesis and
        `force=False`.

        Curator Phase 3b (2026-05-13). Uses fast model. Returns the
        stance dict on success, None on skip/failure.
        """
        # Need a thesis to score against.
        mm = self.get_mental_model(notebook_id)
        if not mm:
            logger.debug(
                f"[CuratorBrain] score_source_stance({notebook_id[:8]}, {source_id[:8]}): "
                f"no mental model, skipping"
            )
            return None
        thesis = (mm.get("thesis") or "").strip()
        if not thesis:
            logger.debug(
                f"[CuratorBrain] score_source_stance({notebook_id[:8]}, {source_id[:8]}): "
                f"mental model has no thesis, skipping"
            )
            return None
        # Phase 3b hotfix (2026-05-13): removed the 0.3 thesis-confidence
        # floor. Even a low-confidence inferred thesis is better than no
        # scoring at all — the resulting stance confidence reflects the
        # uncertainty naturally. User explicitly reported the floor caused
        # stances to silently never appear on freshly-inferred notebooks.

        thesis_hash = self._thesis_hash(thesis)

        # Skip-if-current
        if not force:
            existing = self.get_stance(source_id, notebook_id)
            if existing and existing.get("scored_thesis_hash") == thesis_hash:
                return existing

        # Pull the source content from source_store.
        try:
            from storage.source_store import source_store
            source = await source_store.get(source_id)
        except Exception as e:
            logger.warning(f"[CuratorBrain] score_source_stance: source_store.get failed: {e}")
            return None

        if not source:
            logger.debug(f"[CuratorBrain] score_source_stance: source {source_id} not found")
            return None

        title = source.get("filename") or source.get("title") or source.get("url") or "Untitled"
        content = source.get("content") or ""
        # Snippet bound: leading 1500 chars is enough for a stance call.
        snippet = content[:1500] if content else ""
        if not snippet and title == "Untitled":
            # Nothing to score against.
            return None

        prompt = (
            f"A researcher's notebook has this thesis:\n"
            f"\"{thesis}\"\n"
            f"\n"
            f"Classify how the following source relates to that thesis. Use ONE of:\n"
            f"  - supports   : the source provides evidence FOR the thesis\n"
            f"  - contradicts: the source provides evidence AGAINST the thesis\n"
            f"  - tangential : the source is related but doesn't argue for or against\n"
            f"  - off_topic  : the source is unrelated to the thesis\n"
            f"\n"
            f"Source title: {title[:200]}\n"
            f"Source excerpt:\n{snippet}\n"
            f"\n"
            f"Be honest — if the source has nothing to do with the thesis it is "
            f"off_topic, not 'tangential.' If you're uncertain, lean tangential.\n"
            f"\n"
            f"Respond with ONLY a JSON object: "
            f"{{\"stance\": \"...\", \"confidence\": 0.0-1.0, "
            f"\"rationale\": \"one short sentence\"}}. "
            f"No markdown, no commentary."
        )

        from config import settings as _settings
        try:
            from utils.json_repair import robust_json_parse
            result = await ollama_service.generate(
                prompt=prompt,
                system="You output only valid JSON for stance classification.",
                model=_settings.ollama_fast_model,
                temperature=0.2,
                timeout=30.0,
            )
            raw = (result.get("response") or "").strip()
        except Exception as e:
            logger.warning(f"[CuratorBrain] score_source_stance LLM call failed: {e}")
            return None

        parsed = robust_json_parse(raw, expect="object", fallback=None, label="StanceScorer")
        if not isinstance(parsed, dict):
            logger.warning(f"[CuratorBrain] score_source_stance: parse failed; raw={raw[:200]!r}")
            return None

        stance_raw = str(parsed.get("stance", "")).strip().lower()
        # Normalize common variants
        stance_map = {
            "support": "supports",
            "supports": "supports",
            "supporting": "supports",
            "agree": "supports",
            "agrees": "supports",
            "for": "supports",
            "contradict": "contradicts",
            "contradicts": "contradicts",
            "contradicting": "contradicts",
            "disagree": "contradicts",
            "disagrees": "contradicts",
            "against": "contradicts",
            "tangent": "tangential",
            "tangential": "tangential",
            "related": "tangential",
            "off-topic": "off_topic",
            "offtopic": "off_topic",
            "off_topic": "off_topic",
            "unrelated": "off_topic",
        }
        stance = stance_map.get(stance_raw)
        if not stance:
            logger.warning(
                f"[CuratorBrain] score_source_stance: unrecognized stance {stance_raw!r}; "
                f"defaulting to tangential"
            )
            stance = "tangential"

        try:
            confidence = float(parsed.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        _rat = parsed.get("rationale", "")
        rationale = _rat.strip()[:300] if isinstance(_rat, str) else ""

        ok = self.upsert_stance(
            source_id=source_id,
            notebook_id=notebook_id,
            stance=stance,
            confidence=confidence,
            rationale=rationale,
            thesis_hash=thesis_hash,
        )
        if not ok:
            return None

        # Curator Phase 3c: emit stance_scored so the event-bus consumer
        # can decide whether to fire an overwatch aside (for new
        # high-confidence contradictions, etc.). Fire-and-forget.
        try:
            from services.curator_event_bus import event_bus
            event_bus.emit_now(
                actor="@curator",
                action="stance_scored",
                notebook_id=notebook_id,
                payload={
                    "source_id": source_id,
                    "stance": stance,
                    "confidence": confidence,
                    "rationale": rationale,
                    "scored_thesis_hash": thesis_hash,
                },
                outcome="success",
            )
        except Exception:
            # Observability must not break scoring.
            pass

        return self.get_stance(source_id, notebook_id)

    async def rescore_notebook_stances(
        self,
        notebook_id: str,
        batch_size: int = 5,
        batch_sleep_s: float = 1.0,
    ) -> Dict[str, int]:
        """Walk all sources in the notebook, scoring each against the current
        thesis. Throttled: `batch_size` concurrent calls, then sleep
        `batch_sleep_s` seconds. Sources whose existing thesis_hash matches
        the current thesis are skipped (idempotent).

        Returns {"scored": N, "skipped": M, "failed": K}.
        """
        from storage.source_store import source_store

        try:
            sources = await source_store.list(notebook_id)
        except Exception as e:
            logger.warning(f"[CuratorBrain] rescore_notebook_stances: list failed: {e}")
            return {"scored": 0, "skipped": 0, "failed": 0}

        stats = {"scored": 0, "skipped": 0, "failed": 0}
        # Walk in batches
        ids = [s.get("id") for s in sources if s.get("id")]
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            results = await asyncio.gather(
                *(self.score_source_stance(notebook_id, sid, force=False) for sid in batch),
                return_exceptions=True,
            )
            for sid, res in zip(batch, results):
                if isinstance(res, Exception):
                    stats["failed"] += 1
                elif res is None:
                    stats["failed"] += 1
                else:
                    # If scored_thesis_hash matches current, it was a no-op skip.
                    existing_before_batch = False  # we can't cheaply tell; treat as scored
                    stats["scored"] += 1
            if i + batch_size < len(ids):
                await asyncio.sleep(batch_sleep_s)

        logger.info(
            f"[CuratorBrain] rescore_notebook_stances({notebook_id}): "
            f"scored={stats['scored']} failed={stats['failed']}"
        )
        return stats
