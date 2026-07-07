"""DigestMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class DigestMixin:
    def mark_notebook_dirty(self, notebook_id: str, name: str = "") -> None:
        """
        Called after source ingestion. Marks digest as needing rebuild.
        If the notebook doesn't have a row yet, creates a placeholder so
        get_dirty_notebooks() can find it.
        """
        try:
            existing = self._conn.execute(
                "SELECT notebook_id FROM notebook_digests WHERE notebook_id = ?",
                (notebook_id,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE notebook_digests SET dirty = 1 WHERE notebook_id = ?",
                    (notebook_id,)
                )
            else:
                self._conn.execute(
                    """INSERT OR IGNORE INTO notebook_digests
                       (notebook_id, name, dirty, last_updated)
                       VALUES (?, ?, 1, ?)""",
                    (notebook_id, name or notebook_id[:8], datetime.utcnow().isoformat())
                )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"[CuratorBrain] mark_notebook_dirty failed (non-fatal): {e}")

    def get_dirty_notebooks(self) -> List[str]:
        """Which notebooks need their digest rebuilt?"""
        try:
            rows = self._conn.execute(
                "SELECT notebook_id FROM notebook_digests WHERE dirty = 1"
            ).fetchall()
            return [r["notebook_id"] for r in rows]
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_dirty_notebooks failed (non-fatal): {e}")
            return []

    async def rebuild_notebook_digest(self, notebook_id: str) -> bool:
        """
        Rebuild one notebook's digest from existing knowledge graph + sources.

        READS FROM: knowledge_graph.py LanceDB, source_store, notebook_store.
        WRITES TO:  brain.db only.

        Returns True on success, False on any error (non-fatal).
        """
        try:
            from services.knowledge_graph import knowledge_graph_service as kg
            from storage.source_store import source_store
            from storage.notebook_store import notebook_store

            # 1. Notebook metadata
            notebook = await notebook_store.get(notebook_id)
            nb_name = notebook.get("title", "Untitled") if notebook else "Untitled"

            # 2. Collector subject (best-effort)
            subject = ""
            try:
                from agents.collector import get_collector
                cfg = get_collector(notebook_id).get_config()
                subject = cfg.subject if hasattr(cfg, "subject") and cfg.subject else ""
            except Exception:
                pass

            # 3. Knowledge graph data (already extracted, already clustered)
            try:
                graph_data = await kg.get_graph_data(notebook_id=notebook_id)
                cluster_names = [c.name for c in graph_data.clusters]
                top_concepts = sorted(
                    graph_data.nodes,
                    key=lambda n: n.metadata.get("frequency", 0),
                    reverse=True
                )[:10]
                top_concept_labels = [n.label for n in top_concepts]
            except Exception as e:
                logger.debug(f"[CuratorBrain] KG data unavailable for {notebook_id}: {e}")
                cluster_names = []
                top_concept_labels = []

            # 4. Cross-notebook concepts (concepts shared with ≥1 other notebook)
            cross_concept_names: List[str] = []
            try:
                concepts_table = kg.db.open_table("concepts")
                all_concepts_df = concepts_table.to_pandas()
                nb_concepts = all_concepts_df[
                    all_concepts_df["source_notebook_ids"].apply(
                        lambda x: notebook_id in json.loads(x)
                    )
                ]
                cross_nb = nb_concepts[
                    nb_concepts["source_notebook_ids"].apply(
                        lambda x: len(json.loads(x)) >= 2
                    )
                ]
                cross_concept_names = cross_nb["name"].tolist()[:10]
            except Exception as e:
                logger.debug(f"[CuratorBrain] Cross-concept scan failed (non-fatal): {e}")

            # 5. Recent sources (already computed at ingestion)
            sources: List[Dict] = []
            recent_titles: List[str] = []
            try:
                sources = await source_store.list(notebook_id)
                recent = sorted(sources, key=lambda s: s.get("created_at", ""), reverse=True)[:5]
                recent_titles = [s.get("title", s.get("filename", ""))[:80] for s in recent]
            except Exception as e:
                logger.debug(f"[CuratorBrain] Source list failed (non-fatal): {e}")

            # 6. ONE Phi4-Mini call to synthesize (uses already-resident fast model)
            summary = ""
            if cluster_names or top_concept_labels or recent_titles:
                prompt = (
                    f"Summarize this research notebook's current state in 3-4 sentences.\n\n"
                    f"Notebook: {nb_name}"
                    f"{f' (tracking: {subject})' if subject else ''}\n"
                    f"Sources: {len(sources)} total"
                    f"{f', recent additions: {chr(44).join(recent_titles)}' if recent_titles else ''}\n"
                    f"Key themes (from clustering): "
                    f"{', '.join(cluster_names) if cluster_names else 'not yet clustered'}\n"
                    f"Top concepts: "
                    f"{', '.join(top_concept_labels) if top_concept_labels else 'none extracted yet'}\n"
                    f"Cross-notebook concepts (shared with other notebooks): "
                    f"{', '.join(cross_concept_names) if cross_concept_names else 'none'}\n\n"
                    f"Write a concise summary covering: what this notebook is about, its current "
                    f"research direction, and any notable patterns. Be specific — use actual topic "
                    f"and concept names."
                )
                try:
                    # WS1 (2026-06-23): yield to an active foreground op (chat/
                    # visual) — curator digest rebuilds run post-upload and must
                    # not compete with the user's gemma query. Deadlock-proof.
                    from services.memory_steward import await_background_clearance
                    await await_background_clearance()
                    response = await ollama_service.generate(
                        prompt=prompt,
                        model=settings.ollama_fast_model,
                        temperature=0.3,
                        timeout=30.0,
                        num_predict=200,
                    )
                    summary = response.get("response", "").strip()
                    # Discard error strings
                    if summary.startswith(("Error:", "Request timed out")):
                        summary = ""
                    # Blank JSON/think leakage so the code-built fallback below
                    # runs instead of storing raw JSON in current_summary
                    # (highest-blast-radius curator prose field). (2026-07-07)
                    from utils.json_repair import sanitize_prose_output
                    summary = sanitize_prose_output(summary)
                except Exception as e:
                    logger.debug(f"[CuratorBrain] Digest LLM call failed (non-fatal): {e}")

            # 7. Fallback summary if LLM failed or had nothing to work with
            if not summary and (cluster_names or top_concept_labels):
                parts = []
                if cluster_names:
                    parts.append(f"Key themes: {', '.join(cluster_names[:5])}")
                if top_concept_labels:
                    parts.append(f"Top concepts: {', '.join(top_concept_labels[:5])}")
                summary = f"{nb_name}: {'. '.join(parts)}."

            # 8. Store in brain.db
            previous_row = self._conn.execute(
                "SELECT current_summary FROM notebook_digests WHERE notebook_id = ?",
                (notebook_id,)
            ).fetchone()
            previous_summary = previous_row["current_summary"] if previous_row else None

            now_iso = datetime.utcnow().isoformat()
            self._conn.execute(
                """INSERT OR REPLACE INTO notebook_digests
                   (notebook_id, name, subject, current_summary, key_themes, key_entities,
                    cross_notebook_concepts, source_count, last_updated, previous_summary, dirty)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    notebook_id, nb_name, subject, summary,
                    json.dumps(cluster_names),
                    json.dumps(top_concept_labels),
                    json.dumps(cross_concept_names),
                    len(sources),
                    now_iso,
                    previous_summary,
                )
            )
            self._conn.commit()
            logger.info(f"[CuratorBrain] Digest built for '{nb_name}' ({notebook_id[:8]})")
            return True

        except Exception as e:
            logger.error(f"[CuratorBrain] rebuild_notebook_digest failed for {notebook_id}: {e}")
            return False

    def get_digest(self, notebook_id: str) -> Optional[Dict]:
        """Get a notebook's current digest. Returns None if not built yet."""
        try:
            row = self._conn.execute(
                "SELECT * FROM notebook_digests WHERE notebook_id = ?",
                (notebook_id,)
            ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_digest failed (non-fatal): {e}")
            return None

    def get_all_digests(self, exclude: Optional[str] = None) -> List[Dict]:
        """Get all notebook digests, optionally excluding one."""
        try:
            if exclude:
                rows = self._conn.execute(
                    "SELECT * FROM notebook_digests WHERE notebook_id != ?", (exclude,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM notebook_digests").fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_all_digests failed (non-fatal): {e}")
            return []
