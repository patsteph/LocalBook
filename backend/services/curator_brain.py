"""
Curator Brain — Pre-computed, evolving understanding of the user's research.

Reads from existing systems (knowledge_graph.py LanceDB, source_store).
Writes ONLY to its own brain.db (SQLite WAL) and brain LanceDB.
Never touches the knowledge graph, RAG pipeline, or memory store.

Design principle: every public method is safe to call even if the brain is
empty or partially built. All fallback paths return gracefully so existing
Curator behavior is never degraded.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Any

import lancedb

from config import settings
from services.ollama_client import ollama_client
from utils.singleflight import KeyedSingleflight

logger = logging.getLogger(__name__)

# PB-1b: dedup per-notebook background re-score so rapid thesis edits don't launch
# concurrent rescores for the same notebook. Audit ref: 10_plan_of_attack PB-1b.
_stance_rescore_sf = KeyedSingleflight("stance-rescore")


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

            -- Entity-keyed cross-notebook insights — formerly in
            -- curator_insights.json, migrated 2026-05-12 (Curator Phase 1).
            -- Distinct from `reflections`: insights are short, entity-anchored,
            -- and generated by discover_cross_notebook_patterns; reflections
            -- are LLM-generated narrative observations.
            CREATE TABLE IF NOT EXISTS insights (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_type    TEXT NOT NULL,
                entity          TEXT,
                notebooks       TEXT DEFAULT '[]',
                summary         TEXT NOT NULL,
                confidence      REAL DEFAULT 0.5,
                created_at      TEXT NOT NULL,
                surfaced_count  INTEGER DEFAULT 0,
                last_surfaced   TEXT,
                dismissed       INTEGER DEFAULT 0,
                thumbs_up       INTEGER DEFAULT 0
            );

            -- Event bus persistence — every @-prefix agent emits an event
            -- after its action completes. Brain's consumer loop reads from
            -- the in-memory queue; this table is the durable replay log
            -- in case the brain crashes mid-consume. Added 2026-05-12
            -- (Curator Phase 1). 30-day rolling window — rotation TODO.
            CREATE TABLE IF NOT EXISTS events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                notebook_id     TEXT,
                actor           TEXT NOT NULL,
                action          TEXT NOT NULL,
                intent          TEXT,
                payload         TEXT DEFAULT '{}',
                outcome         TEXT
            );

            -- Engagement telemetry — derived from event bus events plus
            -- explicit UI signals (Phase 2b). Powers smart morning brief
            -- (Phase 5) + calibrated uncertainty (Phase 4). Added Phase 2a.
            -- Local-only; gated by settings.engagement_tracking_enabled.
            CREATE TABLE IF NOT EXISTS engagement_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                notebook_id     TEXT,
                kind            TEXT NOT NULL,
                subject_type    TEXT,
                subject_id      TEXT,
                signal          TEXT NOT NULL,
                payload         TEXT DEFAULT '{}'
            );

            -- Plan-then-act ledger (Curator Phase 2a). Every user-visible
            -- multi-step curator action creates a plan row and one
            -- plan_steps row per step. Status transitions: proposed →
            -- running → completed/cancelled/failed. UI in Phase 2b will
            -- subscribe to plan_* events on the bus for live rendering.
            CREATE TABLE IF NOT EXISTS plans (
                plan_id          TEXT PRIMARY KEY,
                notebook_id      TEXT,
                intent           TEXT NOT NULL,
                summary          TEXT,
                status           TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                started_at       TEXT,
                finished_at      TEXT,
                user_visible     INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS plan_steps (
                step_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id          TEXT NOT NULL,
                seq              INTEGER NOT NULL,
                name             TEXT NOT NULL,
                description      TEXT,
                status           TEXT NOT NULL,
                started_at       TEXT,
                finished_at      TEXT,
                output_summary   TEXT,
                FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
            );

            -- Nag budget log (Curator Phase 3c — 2026-05-13). Records
            -- every proactive surface the curator fires plus the user's
            -- response (thumbs up/down/dismissed). Used by can_fire_nag
            -- to enforce daily cap + per-(kind, notebook) cool-off after
            -- repeated thumbs_down.
            CREATE TABLE IF NOT EXISTS nag_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                kind            TEXT NOT NULL,
                notebook_id     TEXT,
                subject_id      TEXT,
                fired_at        TEXT NOT NULL,
                user_response   TEXT
            );

            -- Pending overwatch asides — text generated by curator's
            -- maybe_fire_dissent_overwatch (and future kinds) that the
            -- next chat reply should surface. Each row is consumed once.
            -- Curator Phase 3c.
            CREATE TABLE IF NOT EXISTS pending_asides (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                notebook_id     TEXT NOT NULL,
                kind            TEXT NOT NULL,
                aside_text      TEXT NOT NULL,
                nag_id          INTEGER,
                created_at      TEXT NOT NULL,
                consumed_at     TEXT
            );

            -- Anticipatory drafts (Curator Phase 6a — 2026-05-13).
            -- The curator pre-drafts Studio outputs overnight for mature
            -- notebooks. Each draft is consumed once (user views it) or
            -- discarded (sets cool-off so we don't redraft).
            CREATE TABLE IF NOT EXISTS draft_outputs (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                notebook_id        TEXT NOT NULL,
                kind               TEXT NOT NULL,
                content_markdown   TEXT NOT NULL,
                source_signal      TEXT,
                created_at         TEXT NOT NULL,
                consumed_at        TEXT,
                discarded_at       TEXT
            );

            -- Manual brief topic suppression (Curator Phase 5 — 2026-05-13).
            -- Substring-match keyword list — when a story title contains
            -- any active key (case-insensitive), it's filtered out of
            -- the morning brief. notebook_id NULL = applies globally
            -- (rare; @curator suppress today defaults to notebook-scoped).
            CREATE TABLE IF NOT EXISTS topic_suppressions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                notebook_id     TEXT,
                topic_key       TEXT NOT NULL,
                suppressed_at   TEXT NOT NULL,
                reason          TEXT,
                UNIQUE(notebook_id, topic_key)
            );

            -- Per-source stance scoring against the notebook thesis
            -- (Curator Phase 3b — 2026-05-13). Each source gets one row
            -- per notebook (UNIQUE constraint). When the thesis changes,
            -- existing rows become stale — detected via scored_thesis_hash
            -- mismatch and re-scored in batches.
            CREATE TABLE IF NOT EXISTS source_stances (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id           TEXT NOT NULL,
                notebook_id         TEXT NOT NULL,
                stance              TEXT NOT NULL,
                confidence          REAL DEFAULT 0.5,
                rationale           TEXT DEFAULT '',
                scored_thesis_hash  TEXT NOT NULL,
                scored_at           TEXT NOT NULL,
                UNIQUE(source_id, notebook_id)
            );

            -- Mental model per notebook (Curator Phase 3a — 2026-05-13).
            -- Curator's evolving understanding of WHAT the user is doing
            -- in each notebook. User can view + edit + pin specific
            -- fields; pinned fields never get overwritten by inference.
            -- Stage values: exploration | gathering | synthesis | drafting | done
            CREATE TABLE IF NOT EXISTS mental_models (
                notebook_id        TEXT PRIMARY KEY,
                thesis             TEXT DEFAULT '',
                goals              TEXT DEFAULT '[]',
                audience           TEXT DEFAULT '',
                stage              TEXT DEFAULT '',
                blocked_on         TEXT DEFAULT '',
                recent_focus       TEXT DEFAULT '',
                pinned_fields      TEXT DEFAULT '[]',
                confidence         REAL DEFAULT 0.0,
                last_inferred_at   TEXT,
                last_user_edit_at  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_connections_status
                ON connections (status, strength DESC);
            CREATE INDEX IF NOT EXISTS idx_reflections_surfaced
                ON reflections (surfaced, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_connections_pair
                ON connections (notebook_a, notebook_b, status);
            CREATE INDEX IF NOT EXISTS idx_insights_active
                ON insights (dismissed, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_insights_entity
                ON insights (entity, dismissed);
            CREATE INDEX IF NOT EXISTS idx_events_recent
                ON events (ts DESC);
            CREATE INDEX IF NOT EXISTS idx_events_actor
                ON events (actor, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_engagement_recent
                ON engagement_events (ts DESC);
            CREATE INDEX IF NOT EXISTS idx_engagement_kind
                ON engagement_events (kind, signal, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_engagement_notebook
                ON engagement_events (notebook_id, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_plans_recent
                ON plans (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_plans_notebook
                ON plans (notebook_id, status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_plan_steps_plan
                ON plan_steps (plan_id, seq);
            CREATE INDEX IF NOT EXISTS idx_mental_models_inferred
                ON mental_models (last_inferred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_stances_notebook
                ON source_stances (notebook_id, stance);
            CREATE INDEX IF NOT EXISTS idx_stances_dissent
                ON source_stances (notebook_id, stance, confidence DESC);
            CREATE INDEX IF NOT EXISTS idx_nag_log_recent
                ON nag_log (fired_at DESC);
            CREATE INDEX IF NOT EXISTS idx_nag_log_kind_nb
                ON nag_log (kind, notebook_id, fired_at DESC);
            CREATE INDEX IF NOT EXISTS idx_pending_asides_active
                ON pending_asides (notebook_id, consumed_at, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_topic_suppressions_active
                ON topic_suppressions (notebook_id, topic_key);
            CREATE INDEX IF NOT EXISTS idx_drafts_active
                ON draft_outputs (notebook_id, consumed_at, discarded_at, created_at DESC);

            -- Per-source rolling reputation (Phase 7.6 prep — 2026-05-23).
            -- Capture-only at first: every source_added / source_approved /
            -- source_rejected event updates this table. Surfacing rule
            -- ("source X dropped from 80% to 20%") lands later once the
            -- table has enough data to be meaningful. One row per
            -- (notebook_id, source_id).
            CREATE TABLE IF NOT EXISTS source_reputation (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                notebook_id       TEXT NOT NULL,
                source_id         TEXT NOT NULL,
                source_label      TEXT DEFAULT '',
                total_events      INTEGER DEFAULT 0,
                approved_count    INTEGER DEFAULT 0,
                rejected_count    INTEGER DEFAULT 0,
                added_count       INTEGER DEFAULT 0,
                rolling_30d_events INTEGER DEFAULT 0,
                rolling_30d_approved INTEGER DEFAULT 0,
                rolling_30d_rejected INTEGER DEFAULT 0,
                lifetime_acceptance_rate REAL DEFAULT 0.0,
                rolling_acceptance_rate REAL DEFAULT 0.0,
                first_seen_at     TEXT,
                last_event_at     TEXT,
                UNIQUE(notebook_id, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_source_reputation_nb
                ON source_reputation (notebook_id, rolling_acceptance_rate);
        """)
        self._conn.commit()
        self._migrate_legacy_insights()

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
                    cur = self._conn.execute(
                        """INSERT INTO connections
                           (notebook_a, notebook_b, connection_type, description,
                            evidence, strength, discovered_at, status)
                           VALUES (?, ?, 'shared_concepts', ?, ?, ?, ?, 'active')""",
                        (nb_a, nb_b, description, evidence_json, strength, now_iso)
                    )
                    new_connections.append({
                        "id": cur.lastrowid,
                        "notebooks": [nb_a, nb_b],
                        "names": [digest_a["name"], digest_b["name"]],
                        "concepts": concepts,
                        "strength": strength,
                        "description": description,
                        "notebook_a": nb_a,
                        "notebook_b": nb_b,
                    })

            self._conn.commit()
            if new_connections:
                logger.info(
                    f"[CuratorBrain] {len(new_connections)} new cross-notebook connection(s) detected"
                )
                # Curator Phase 5: emit a connection_discovered event for
                # each new high-strength connection so the overwatch
                # consumer can queue a pending aside. Fire-and-forget.
                try:
                    from services.curator_event_bus import event_bus
                    for conn in new_connections:
                        if (conn.get("strength") or 0) > 0.7:
                            event_bus.emit_now(
                                actor="@curator",
                                action="connection_discovered",
                                notebook_id=conn.get("notebook_a"),
                                payload={
                                    "connection_id": conn.get("id"),
                                    "notebook_a": conn.get("notebook_a"),
                                    "notebook_b": conn.get("notebook_b"),
                                    "description": conn.get("description", ""),
                                    "strength": conn.get("strength"),
                                },
                                outcome="success",
                            )
                except Exception as _e:
                    logger.debug(f"[CuratorBrain] connection_discovered emit: {_e}")
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
            # 2026-06-15: was `from database import get_db` — wrong path,
            # threw `No module named 'database'` every consolidation cycle
            # and silently skipped wikilink detection.
            from storage.database import get_db
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
    # Insights API (Curator Phase 1 — migrated from curator_insights.json)
    # ------------------------------------------------------------------
    # Insights are short, entity-anchored cross-notebook observations
    # produced by discover_cross_notebook_patterns. Distinct from
    # reflections (LLM-generated narrative) and connections (notebook-pair
    # relationships). Schema mirrors the legacy ProactiveInsight model
    # exactly so curator.py can keep using ProactiveInsight in-memory.

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

    def _migrate_legacy_insights(self) -> None:
        """One-shot: pull curator_insights.json into the insights table.

        Idempotent — if the JSON file is gone (already migrated and
        renamed to .pre-v3-backup) this is a no-op. Run once at init
        time, after table creation.
        """
        legacy_path = Path(settings.data_dir) / "curator_insights.json"
        if not legacy_path.exists():
            return
        try:
            # Don't double-migrate if insights table already has rows.
            existing = self._conn.execute(
                "SELECT COUNT(*) FROM insights"
            ).fetchone()[0]
            if existing > 0:
                logger.info(
                    f"[CuratorBrain] insights table already populated ({existing} rows); "
                    f"backing up legacy file without re-migrating"
                )
                self._backup_legacy_insights(legacy_path)
                return

            data = json.loads(legacy_path.read_text())
            if not isinstance(data, list):
                logger.warning(f"[CuratorBrain] legacy insights file malformed (not a list); skipping")
                return

            count = self.add_insights(data)
            logger.info(f"[CuratorBrain] migrated {count} legacy insights from curator_insights.json")
            self._backup_legacy_insights(legacy_path)
        except Exception as e:
            logger.warning(f"[CuratorBrain] legacy insight migration failed (non-fatal): {e}")

    @staticmethod
    def _backup_legacy_insights(legacy_path: Path) -> None:
        """Rename curator_insights.json → curator_insights.json.pre-v3-backup."""
        backup_path = legacy_path.with_suffix(".json.pre-v3-backup")
        try:
            legacy_path.rename(backup_path)
            logger.info(f"[CuratorBrain] backed up legacy insights → {backup_path.name}")
        except Exception as e:
            logger.warning(f"[CuratorBrain] could not back up legacy insights: {e}")

    # ------------------------------------------------------------------
    # Event Persistence (Curator Phase 1 — backs curator_event_bus.py)
    # ------------------------------------------------------------------
    # The event bus drains its in-memory asyncio.Queue and calls
    # record_event() for each event. This is the durable replay log so a
    # brain restart doesn't lose events that hadn't been consumed yet.

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

    # ------------------------------------------------------------------
    # Engagement Telemetry (Curator Phase 2a — 2026-05-13)
    # ------------------------------------------------------------------
    # engagement_events captures what the user actually interacted with —
    # asked queries, dismissed reflections, clicked brief stories, thumbs
    # up/down on connections. Powers smart morning brief (Phase 5) and
    # calibrated uncertainty (Phase 4). Gated by
    # settings.engagement_tracking_enabled so a user can disable.
    #
    # Kinds (open-ended; new categories OK):
    #   query         — RAG question asked
    #   source        — source-level signal (rejected, approved, viewed)
    #   curator_feature — which @curator intent the user invoked
    #   brief         — morning/weekly brief opened, story clicked, dismissed
    #   reflection    — reflection surfaced + thumbs reaction
    #   connection    — cross-notebook connection thumbs reaction
    #
    # Signals (open-ended): asked, opened, clicked, ignored, dismissed,
    #   rejected, approved, thumbs_up, thumbs_down, invoked, viewed.

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

    # ------------------------------------------------------------------
    # Plan-then-Act Ledger (Curator Phase 2a — 2026-05-13)
    # ------------------------------------------------------------------
    # Every multi-step curator action creates a plan row + N plan_steps
    # rows. Lifecycle: create_plan → start_plan → start_step → complete_step
    # (per step) → … → plan auto-completes when last step done OR
    # explicitly via fail/cancel. Each transition emits a plan_* event
    # on the curator bus; the Phase 2b plan card subscribes via SSE.
    #
    # Failure mode discipline: any DB error returns gracefully (None / False)
    # and logs. Callers tolerate the absence — if the brain isn't tracking
    # the plan, the underlying action still runs to completion.

    def create_plan(
        self,
        intent: str,
        summary: str,
        steps: List[Dict[str, str]],
        notebook_id: Optional[str] = None,
        user_visible: bool = True,
    ) -> Optional[str]:
        """Create a new plan + its steps. Returns plan_id (uuid) or None."""
        import uuid as _uuid
        plan_id = str(_uuid.uuid4())
        now = datetime.utcnow().isoformat()
        try:
            self._conn.execute(
                """INSERT INTO plans
                   (plan_id, notebook_id, intent, summary, status, created_at, user_visible)
                   VALUES (?, ?, ?, ?, 'proposed', ?, ?)""",
                (plan_id, notebook_id, intent, summary, now, 1 if user_visible else 0),
            )
            for i, step in enumerate(steps, start=1):
                self._conn.execute(
                    """INSERT INTO plan_steps
                       (plan_id, seq, name, description, status)
                       VALUES (?, ?, ?, ?, 'pending')""",
                    (plan_id, i, step.get("name", f"step_{i}"), step.get("description")),
                )
            self._conn.commit()
            self._emit_plan_event("plan_created", plan_id, notebook_id, intent=intent)
            return plan_id
        except Exception as e:
            logger.warning(f"[CuratorBrain] create_plan failed: {e}")
            return None

    def start_plan(self, plan_id: str) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                "UPDATE plans SET status = 'running', started_at = ? WHERE plan_id = ? AND status = 'proposed'",
                (now, plan_id),
            )
            self._conn.commit()
            self._emit_plan_event("plan_started", plan_id, None)
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] start_plan failed: {e}")
            return False

    def start_step(self, plan_id: str, seq: int) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plan_steps
                   SET status = 'running', started_at = ?
                   WHERE plan_id = ? AND seq = ? AND status = 'pending'""",
                (now, plan_id, seq),
            )
            self._conn.commit()
            self._emit_plan_event("plan_step_started", plan_id, None,
                                  payload={"seq": seq})
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] start_step failed: {e}")
            return False

    def complete_step(
        self,
        plan_id: str,
        seq: int,
        output_summary: Optional[str] = None,
    ) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plan_steps
                   SET status = 'done', finished_at = ?, output_summary = ?
                   WHERE plan_id = ? AND seq = ?""",
                (now, output_summary, plan_id, seq),
            )
            # Auto-complete the plan if all steps are done.
            row = self._conn.execute(
                """SELECT
                     SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_n,
                     COUNT(*) AS total_n
                   FROM plan_steps WHERE plan_id = ?""",
                (plan_id,),
            ).fetchone()
            self._conn.commit()
            self._emit_plan_event("plan_step_completed", plan_id, None,
                                  payload={"seq": seq, "output_summary": output_summary})
            if row and row["done_n"] == row["total_n"] and row["total_n"] > 0:
                self._conn.execute(
                    """UPDATE plans
                       SET status = 'completed', finished_at = ?
                       WHERE plan_id = ?""",
                    (now, plan_id),
                )
                self._conn.commit()
                self._emit_plan_event("plan_completed", plan_id, None)
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] complete_step failed: {e}")
            return False

    def fail_step(self, plan_id: str, seq: int, reason: str) -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plan_steps
                   SET status = 'failed', finished_at = ?, output_summary = ?
                   WHERE plan_id = ? AND seq = ?""",
                (now, reason, plan_id, seq),
            )
            self._conn.execute(
                """UPDATE plans
                   SET status = 'failed', finished_at = ?
                   WHERE plan_id = ?""",
                (now, plan_id),
            )
            self._conn.commit()
            self._emit_plan_event("plan_failed", plan_id, None,
                                  payload={"seq": seq, "reason": reason})
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] fail_step failed: {e}")
            return False

    def cancel_plan(self, plan_id: str, reason: str = "") -> bool:
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                """UPDATE plans
                   SET status = 'cancelled', finished_at = ?
                   WHERE plan_id = ?""",
                (now, plan_id),
            )
            self._conn.commit()
            self._emit_plan_event("plan_cancelled", plan_id, None,
                                  payload={"reason": reason})
            return True
        except Exception as e:
            logger.warning(f"[CuratorBrain] cancel_plan failed: {e}")
            return False

    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        try:
            plan_row = self._conn.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if not plan_row:
                return None
            steps_rows = self._conn.execute(
                "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY seq",
                (plan_id,),
            ).fetchall()
            return {
                "plan_id": plan_row["plan_id"],
                "notebook_id": plan_row["notebook_id"],
                "intent": plan_row["intent"],
                "summary": plan_row["summary"],
                "status": plan_row["status"],
                "created_at": plan_row["created_at"],
                "started_at": plan_row["started_at"],
                "finished_at": plan_row["finished_at"],
                "user_visible": bool(plan_row["user_visible"]),
                "steps": [
                    {
                        "seq": s["seq"],
                        "name": s["name"],
                        "description": s["description"],
                        "status": s["status"],
                        "started_at": s["started_at"],
                        "finished_at": s["finished_at"],
                        "output_summary": s["output_summary"],
                    }
                    for s in steps_rows
                ],
            }
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_plan failed: {e}")
            return None

    def recent_plans(
        self,
        limit: int = 20,
        notebook_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            clauses: List[str] = []
            params: List[Any] = []
            if notebook_id:
                clauses.append("notebook_id = ?")
                params.append(notebook_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur = self._conn.execute(
                f"""SELECT plan_id, notebook_id, intent, summary, status,
                           created_at, started_at, finished_at, user_visible
                    FROM plans{where}
                    ORDER BY created_at DESC
                    LIMIT ?""",
                params,
            )
            return [
                {
                    "plan_id": r["plan_id"],
                    "notebook_id": r["notebook_id"],
                    "intent": r["intent"],
                    "summary": r["summary"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "started_at": r["started_at"],
                    "finished_at": r["finished_at"],
                    "user_visible": bool(r["user_visible"]),
                }
                for r in cur.fetchall()
            ]
        except Exception as e:
            logger.warning(f"[CuratorBrain] recent_plans failed: {e}")
            return []

    def _emit_plan_event(
        self,
        action: str,
        plan_id: str,
        notebook_id: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
        intent: Optional[str] = None,
    ) -> None:
        """Fire a plan_* event onto the curator event bus.

        If notebook_id is None, looks it up from the plans table so
        downstream consumers (Phase 5 plan_completed overwatch trigger)
        can route by notebook.
        """
        try:
            # Look up notebook_id when caller didn't supply it — costs
            # one cheap indexed query.
            if notebook_id is None:
                try:
                    row = self._conn.execute(
                        "SELECT notebook_id FROM plans WHERE plan_id = ?",
                        (plan_id,),
                    ).fetchone()
                    if row:
                        notebook_id = row["notebook_id"]
                except Exception:
                    pass
            from services.curator_event_bus import event_bus
            base_payload: Dict[str, Any] = {"plan_id": plan_id}
            if payload:
                base_payload.update(payload)
            event_bus.emit_now(
                actor="@curator",
                action=action,
                notebook_id=notebook_id,
                intent=intent,
                payload=base_payload,
                outcome="success",
            )
        except Exception:
            # Plan ledger is observability — never propagate failures.
            pass

    # ------------------------------------------------------------------
    # Mental Model (Curator Phase 3a — 2026-05-13)
    # ------------------------------------------------------------------
    # Per-notebook "what the curator thinks the user is doing." The
    # fields are: thesis, goals (list), audience, stage, blocked_on,
    # recent_focus. Each can be either auto-inferred (last_inferred_at)
    # or user-pinned (in pinned_fields list). Pinned fields are never
    # overwritten by inference.
    #
    # Inference itself lives in infer_mental_model() — that method does
    # the LLM call. This section is the storage CRUD only.

    _MENTAL_MODEL_FIELDS = (
        "thesis",
        "goals",
        "audience",
        "stage",
        "blocked_on",
        "recent_focus",
    )

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
            result = await ollama_client.generate(
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
                    inferred[field] = [str(x).strip() for x in v if x][:6]
                elif isinstance(v, str):
                    # Comma-separated fallback
                    inferred[field] = [g.strip() for g in v.split(",") if g.strip()][:6]
                else:
                    inferred[field] = []
            else:
                inferred[field] = str(v).strip() if v else ""

        try:
            confidence = float(parsed.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        ok = self._upsert_inferred_mental_model(notebook_id, inferred, confidence)
        if not ok:
            return existing or None
        return self.get_mental_model(notebook_id)

    # Minimum source count before the curator attempts inference at all.
    # Below this, the notebook is "too sparse" — any inferred mental
    # model would be hallucinated more than observed. Curator Phase 3a.
    _MENTAL_MODEL_MIN_SOURCES = 5

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

    # ------------------------------------------------------------------
    # Cancellable plan registry (Curator Phase 2b — 2026-05-13)
    # ------------------------------------------------------------------
    # In-memory map of plan_id → asyncio.Event so a UI stop request can
    # signal a running plan to halt at its next natural breakpoint.
    # Lives on the brain instance (process-local) — survives nothing past
    # a backend restart, which is fine: a restart cancels everything
    # implicitly.
    #
    # Discipline:
    # - register_cancellable() before the plan starts running
    # - is_cancelled() at every breakpoint inside the runner
    # - unregister_cancellable() in a finally block when the plan
    #   reaches a terminal state (completed/failed/cancelled). Always
    #   fires so the registry doesn't accumulate dead entries.
    # - trigger_cancel() sets the event; safe to call multiple times.

    def register_cancellable(self, plan_id: str) -> None:
        """Register a plan as cancellable. Idempotent."""
        if not hasattr(self, "_cancellation_events"):
            self._cancellation_events: Dict[str, "asyncio.Event"] = {}
        if plan_id not in self._cancellation_events:
            import asyncio as _asyncio
            self._cancellation_events[plan_id] = _asyncio.Event()

    def is_cancelled(self, plan_id: str) -> bool:
        """Check whether a cancel signal has been issued for this plan."""
        evt = getattr(self, "_cancellation_events", {}).get(plan_id)
        return bool(evt and evt.is_set())

    def trigger_cancel(self, plan_id: str) -> bool:
        """Signal cancellation for plan_id. Returns True if the plan was
        registered as cancellable (i.e. it was running), False otherwise.

        Called by the HTTP cancel endpoint when the user clicks Stop.
        Safe to call repeatedly.
        """
        evt = getattr(self, "_cancellation_events", {}).get(plan_id)
        if evt is None:
            return False
        evt.set()
        return True

    def unregister_cancellable(self, plan_id: str) -> None:
        """Clean up the registry entry. Called in finally after the
        plan reaches a terminal state.
        """
        evts = getattr(self, "_cancellation_events", None)
        if evts is not None:
            evts.pop(plan_id, None)

    # ------------------------------------------------------------------
    # Source Stance Scoring (Curator Phase 3b — 2026-05-13)
    # ------------------------------------------------------------------
    # Each source in a notebook with an inferred thesis gets one stance
    # row (UNIQUE per source+notebook): supports / contradicts /
    # tangential / off_topic, with confidence + a one-sentence rationale.
    # When the thesis changes, scored_thesis_hash mismatch triggers
    # re-scoring (batched, throttled).
    #
    # scorer LLM call lives in score_source_stance() below the CRUD.

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

    # ------------------------------------------------------------------
    # Nag Budget (Curator Phase 3c — 2026-05-13)
    # ------------------------------------------------------------------
    # Every proactive curator surface (dissent in chat, overwatch asides,
    # future: connection callouts) records a nag_log row when it fires.
    # The user can thumbs-up/down/dismiss each surface. can_fire_nag
    # enforces:
    #   - Daily cap (default 3 across ALL kinds — keeps the assistant
    #     from feeling chatty)
    #   - Per-(kind, notebook) cool-off after 2 thumbs_down in 7 days

    _NAG_VALID_RESPONSES = ("up", "down", "dismissed")

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

    # ------------------------------------------------------------------
    # Topic Suppressions (Curator Phase 5 — 2026-05-13)
    # ------------------------------------------------------------------
    # User can say `@curator stop showing me crypto stories` — that
    # records a substring-match keyword that filters story titles in
    # morning brief generation. Case-insensitive contains.

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

    # ------------------------------------------------------------------
    # Anticipatory Drafts (Curator Phase 6a — 2026-05-13)
    # ------------------------------------------------------------------
    # The curator pre-drafts Studio content for mature notebooks. Drafts
    # surface in the morning brief as a one-line callout; user runs
    # @curator show draft to view or @curator discard draft to reject.
    # Discard sets a 14-day cool-off via is_drafting_suppressed.

    _DRAFT_DISCARD_COOLOFF_DAYS = 14

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

    # ------------------------------------------------------------------
    # Pending Asides (Curator Phase 3c — 2026-05-13)
    # ------------------------------------------------------------------
    # When event-bus triggers an overwatch aside (e.g. new contradicting
    # source), the text is queued here. The next @curator chat reply
    # picks one up and consumes it. Each row consumed once.

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
            result = await ollama_client.generate(
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

        rationale = str(parsed.get("rationale", "")).strip()[:300]

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

    # ------------------------------------------------------------------
    # Phase 7 readiness helpers (2026-05-23)
    # ------------------------------------------------------------------
    #
    # These read paths are what Phase 7.2 (voice scoring) and Phase 7.6
    # (source reputation) will call when their surfacing rules ship.
    # Adding them now so the engagement_events table is being queried
    # the same way at capture-time and at surface-time.

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


# Module-level singleton — imported by curator.py, memory_manager.py, ingestion pipeline
curator_brain = CuratorBrain()
