"""ConnectionsMixin — extracted from the former services/curator_brain.py (Wave 4 split)."""
from ._models import *  # noqa: F401,F403


class ConnectionsMixin:
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
