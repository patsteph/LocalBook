"""
Curator Brain — Pre-computed, evolving understanding of the user's research.

Reads from existing systems (knowledge_graph.py LanceDB, source_store).
Writes ONLY to its own brain.db (SQLite WAL) and brain LanceDB.
Never touches the knowledge graph, RAG pipeline, or memory store.

Design principle: every public method is safe to call even if the brain is
empty or partially built. All fallback paths return gracefully so existing
Curator behavior is never degraded.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Any

import lancedb

from config import settings
from services.ollama_client import ollama_client

logger = logging.getLogger(__name__)


class CuratorBrain:
    """
    The Curator's compiled knowledge — reads from existing systems, never writes to them.

    Lifecycle:
      - mark_notebook_dirty()  → called by ingestion pipeline (one line)
      - rebuild_notebook_digest() → called by memory_manager Tier 3
      - detect_connections()   → called by memory_manager Tier 3 after digests built
      - get_brief_context()    → called by curator.py morning brief
      - get_digest()           → called by curator.py overwatch fast path
    """

    # ------------------------------------------------------------------
    # Init & Schema
    # ------------------------------------------------------------------

    def __init__(self):
        brain_dir = Path(settings.data_dir) / "curator_brain"
        brain_dir.mkdir(parents=True, exist_ok=True)

        self._db_path = brain_dir / "brain.db"
        self._conn = self._open_db()
        self._init_tables()

        # Separate LanceDB for embedding-searchable summaries/reflections.
        # NOT the main LanceDB, NOT the knowledge graph LanceDB.
        self._lancedb_path = brain_dir / "lancedb"
        self._lancedb_path.mkdir(parents=True, exist_ok=True)
        try:
            self.vectors = lancedb.connect(str(self._lancedb_path))
        except Exception as e:
            logger.warning(f"[CuratorBrain] LanceDB init failed (non-fatal): {e}")
            self.vectors = None

    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row      # rows behave like dicts
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS notebook_digests (
                notebook_id  TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                subject      TEXT DEFAULT '',
                current_summary      TEXT,
                key_themes           TEXT DEFAULT '[]',
                key_entities         TEXT DEFAULT '[]',
                cross_notebook_concepts TEXT DEFAULT '[]',
                source_count         INTEGER DEFAULT 0,
                last_source_added    TEXT,
                last_updated         TEXT,
                previous_summary     TEXT,
                dirty                INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS connections (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                notebook_a       TEXT NOT NULL,
                notebook_b       TEXT NOT NULL,
                connection_type  TEXT NOT NULL DEFAULT 'shared_concepts',
                description      TEXT NOT NULL,
                evidence         TEXT DEFAULT '[]',
                strength         REAL DEFAULT 0.5,
                discovered_at    TEXT NOT NULL,
                last_validated   TEXT,
                status           TEXT DEFAULT 'active',
                surfaced_count   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS reflections (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                content             TEXT NOT NULL,
                evidence_notebooks  TEXT DEFAULT '[]',
                evidence_concepts   TEXT DEFAULT '[]',
                importance          INTEGER DEFAULT 3,
                created_at          TEXT NOT NULL,
                surfaced            INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS provenance (
                brain_artifact_type TEXT NOT NULL,
                brain_artifact_id   TEXT NOT NULL,
                source_type         TEXT NOT NULL,
                source_id           TEXT NOT NULL,
                notebook_id         TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_connections_status
                ON connections (status, strength DESC);
            CREATE INDEX IF NOT EXISTS idx_reflections_surfaced
                ON reflections (surfaced, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_connections_pair
                ON connections (notebook_a, notebook_b, status);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Dirty Tracking
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Digest Building
    # ------------------------------------------------------------------

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
                    response = await ollama_client.generate(
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

    # ------------------------------------------------------------------
    # Connection Detection
    # ------------------------------------------------------------------

    async def detect_connections(self) -> List[Dict]:
        """
        Find cross-notebook connections using existing knowledge graph data.

        No Kuzu needed — concepts already track source_notebook_ids.
        Inserts new connections and updates strength/evidence on existing ones.
        Returns list of newly found connections.
        """
        try:
            from services.knowledge_graph import knowledge_graph_service as kg

            concepts_table = kg.db.open_table("concepts")
            df = concepts_table.to_pandas()

            if df.empty:
                return []

            # Find concepts that appear in 2+ notebooks
            df["nb_list"] = df["source_notebook_ids"].apply(json.loads)
            df["nb_count"] = df["nb_list"].apply(len)
            cross = df[df["nb_count"] >= 2].sort_values("frequency", ascending=False)

            if cross.empty:
                return []

            # Group shared concepts by notebook pair
            pair_concepts: Dict[tuple, List[str]] = {}
            for _, row in cross.iterrows():
                for a, b in combinations(sorted(row["nb_list"]), 2):
                    key = (a, b)
                    pair_concepts.setdefault(key, [])
                    pair_concepts[key].append(row["name"])

            new_connections: List[Dict] = []
            now_iso = datetime.utcnow().isoformat()

            for (nb_a, nb_b), concepts in pair_concepts.items():
                if len(concepts) < 2:
                    continue

                digest_a = self.get_digest(nb_a)
                digest_b = self.get_digest(nb_b)
                if not digest_a or not digest_b:
                    continue

                strength = min(1.0, len(concepts) * 0.15)
                evidence_json = json.dumps(concepts[:10])
                description = (
                    f"{digest_a['name']} and {digest_b['name']} share "
                    f"{len(concepts)} concept{'s' if len(concepts) != 1 else ''}: "
                    f"{', '.join(concepts[:5])}"
                )

                existing = self._conn.execute(
                    """SELECT id FROM connections
                       WHERE notebook_a = ? AND notebook_b = ? AND status = 'active'""",
                    (nb_a, nb_b)
                ).fetchone()

                if existing:
                    # Update strength and evidence if the connection has grown
                    self._conn.execute(
                        """UPDATE connections
                           SET evidence = ?, strength = ?, description = ?, last_validated = ?
                           WHERE id = ?""",
                        (evidence_json, strength, description, now_iso, existing["id"])
                    )
                else:
                    self._conn.execute(
                        """INSERT INTO connections
                           (notebook_a, notebook_b, connection_type, description,
                            evidence, strength, discovered_at, status)
                           VALUES (?, ?, 'shared_concepts', ?, ?, ?, ?, 'active')""",
                        (nb_a, nb_b, description, evidence_json, strength, now_iso)
                    )
                    new_connections.append({
                        "notebooks": [nb_a, nb_b],
                        "names": [digest_a["name"], digest_b["name"]],
                        "concepts": concepts,
                        "strength": strength,
                    })

            self._conn.commit()
            if new_connections:
                logger.info(
                    f"[CuratorBrain] {len(new_connections)} new cross-notebook connection(s) detected"
                )
            return new_connections

        except Exception as e:
            logger.error(f"[CuratorBrain] detect_connections failed: {e}")
            return []

    async def detect_wikilink_connections(self) -> List[Dict]:
        """
        Find connections from user-created wikilinks.
        These are highest-confidence since the user explicitly linked them.
        """
        try:
            from database import get_db
            db = get_db()
            conn = db.get_connection()
            
            # Get all notes with wikilinks
            # We query the main localbook.db (not brain.db)
            rows = conn.execute(
                "SELECT id, notebook_id, wikilinks_out FROM canvas_notes WHERE wikilinks_out != '[]'"
            ).fetchall()
            
            new_connections: List[Dict] = []
            now_iso = datetime.utcnow().isoformat()
            
            for row in rows:
                targets = json.loads(row['wikilinks_out'])
                for target in targets:
                    # Find the target note or source's notebook
                    # Target can be a note title or an ID
                    target_row = conn.execute(
                        "SELECT notebook_id FROM canvas_notes WHERE id = ? OR title = ?",
                        (target, target)
                    ).fetchone()
                    
                    if not target_row:
                        # Try searching sources as well
                        from storage.source_store import source_store
                        # This is a bit expensive in a loop, but wikilinks are relatively rare
                        # and we only do this in Tier 3.
                        # For now, let's keep it simple.
                        continue

                    target_notebook_id = target_row['notebook_id']
                    
                    if target_notebook_id and target_notebook_id != row['notebook_id']:
                        # Cross-notebook wikilink — high-confidence connection
                        digest_a = self.get_digest(row['notebook_id'])
                        digest_b = self.get_digest(target_notebook_id)
                        
                        if not digest_a or not digest_b:
                            continue

                        description = f"User explicitly linked notes across {digest_a['name']} and {digest_b['name']}"
                        
                        existing = self._conn.execute(
                            """SELECT id FROM connections
                               WHERE notebook_a = ? AND notebook_b = ? AND status = 'active'
                               AND connection_type = 'user_wikilink'""",
                            (row['notebook_id'], target_notebook_id)
                        ).fetchone()
                        
                        if not existing:
                            self._conn.execute(
                                """INSERT INTO connections
                                   (notebook_a, notebook_b, connection_type, description,
                                    evidence, strength, discovered_at, status)
                                   VALUES (?, ?, 'user_wikilink', ?, ?, 0.9, ?, 'active')""",
                                (row['notebook_id'], target_notebook_id, description, json.dumps([target]), now_iso)
                            )
                            new_connections.append({
                                "notebooks": [row['notebook_id'], target_notebook_id],
                                "names": [digest_a["name"], digest_b["name"]],
                                "type": "user_wikilink"
                            })
            
            self._conn.commit()
            if new_connections:
                logger.info(f"[CuratorBrain] {len(new_connections)} new wikilink connections detected")
            return new_connections
            
        except Exception as e:
            logger.error(f"[CuratorBrain] detect_wikilink_connections failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

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

    def get_active_connections(self) -> List[Dict]:
        """Get all active cross-notebook connections, strongest first."""
        try:
            rows = self._conn.execute(
                "SELECT * FROM connections WHERE status = 'active' ORDER BY strength DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_active_connections failed (non-fatal): {e}")
            return []

    def get_connections_for_notebook(self, notebook_id: str) -> List[Dict]:
        """Get active connections involving a specific notebook."""
        try:
            rows = self._conn.execute(
                """SELECT * FROM connections
                   WHERE status = 'active'
                     AND (notebook_a = ? OR notebook_b = ?)
                   ORDER BY strength DESC""",
                (notebook_id, notebook_id)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"[CuratorBrain] get_connections_for_notebook failed (non-fatal): {e}")
            return []

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

    # ------------------------------------------------------------------
    # Reflections
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # User Feedback (Phase 4B foundation)
    # ------------------------------------------------------------------

    def dismiss_connection(self, connection_id: int) -> bool:
        """Mark a connection as dismissed — never show again."""
        try:
            self._conn.execute(
                "UPDATE connections SET status = 'dismissed_by_user' WHERE id = ?",
                (connection_id,)
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] dismiss_connection failed: {e}")
            return False

    def thumbs_up_connection(self, connection_id: int) -> bool:
        """Boost a connection's strength — user found it valuable."""
        try:
            self._conn.execute(
                """UPDATE connections
                   SET strength = MIN(1.0, strength + 0.1),
                       surfaced_count = surfaced_count + 1
                   WHERE id = ?""",
                (connection_id,)
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] thumbs_up_connection failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Phase 4A: Reflection Generation (Generative Agents Pattern)
    # ------------------------------------------------------------------

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

            response = await ollama_client.generate(
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

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

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


# Module-level singleton — imported by curator.py, memory_manager.py, ingestion pipeline
curator_brain = CuratorBrain()
