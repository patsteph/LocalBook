"""ProvenanceMixin — the 'made-from' derivation edges (Journey Canvas Walk).

Finally writes the `provenance` table (schema in _base.py) that shipped DEAD: a
generated artifact (infographic / document / podcast / quiz …) is made FROM
notebook sources. Each row is one made-from edge, so the Canvas can draw the
provenance (aqua) edges and answer "what was this built from?" / "what did this
source produce?". Never raises — a provenance failure must never break generation.
"""
from ._models import *  # noqa: F401,F403


def _coerce_source_ids(sources: Optional[List[Any]]) -> List[str]:
    """Extract REAL source-id strings from a mixed list (id strings or the
    `{n, source_id, title}` dicts the generators pass). Dedup, drop blanks, and
    drop entries with no real source_id — a made-from edge needs a source identity,
    not a display index."""
    out: List[str] = []
    seen = set()
    for s in (sources or []):
        sid = None
        if isinstance(s, str):
            sid = s
        elif isinstance(s, dict):
            sid = s.get("source_id") or s.get("id")
        if sid is None:
            continue
        sid = str(sid).strip()
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


class ProvenanceMixin:
    def record_provenance(
        self,
        artifact_type: str,
        artifact_id: str,
        sources: Optional[List[Any]] = None,
        notebook_id: Optional[str] = None,
        source_type: str = "source",
    ) -> int:
        """Record made-from edges for a generated artifact — one row per source.
        Regenerate-safe: clears this artifact's prior edges first so a re-run
        doesn't accumulate duplicates. Returns the edge count (0 on failure/no ids)."""
        try:
            if not artifact_id or not artifact_type:
                return 0
            ids = _coerce_source_ids(sources)
            self._conn.execute(
                "DELETE FROM provenance WHERE brain_artifact_type = ? AND brain_artifact_id = ?",
                (artifact_type, str(artifact_id)),
            )
            for sid in ids:
                self._conn.execute(
                    """INSERT INTO provenance
                       (brain_artifact_type, brain_artifact_id, source_type, source_id, notebook_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (artifact_type, str(artifact_id), source_type, sid, notebook_id),
                )
            self._conn.commit()
            return len(ids)
        except Exception as e:
            logger.warning(f"[CuratorBrain] record_provenance failed: {e}")
            return 0

    def get_provenance(self, artifact_id: str) -> List[Dict[str, Any]]:
        """The made-from edges for ONE artifact (what it was built from)."""
        try:
            rows = self._conn.execute(
                """SELECT brain_artifact_type, brain_artifact_id, source_type, source_id, notebook_id
                   FROM provenance WHERE brain_artifact_id = ?""",
                (str(artifact_id),),
            ).fetchall()
            return [
                {"artifact_type": r[0], "artifact_id": r[1], "source_type": r[2],
                 "source_id": r[3], "notebook_id": r[4]}
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_provenance failed: {e}")
            return []

    def get_derived_from_source(
        self, source_id: str, notebook_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """The REVERSE edge: distinct artifacts derived FROM a given source."""
        try:
            clauses = ["source_id = ?"]
            params: List[Any] = [str(source_id)]
            if notebook_id:
                clauses.append("notebook_id = ?")
                params.append(notebook_id)
            rows = self._conn.execute(
                "SELECT DISTINCT brain_artifact_type, brain_artifact_id FROM provenance WHERE "
                + " AND ".join(clauses),
                params,
            ).fetchall()
            return [{"artifact_type": r[0], "artifact_id": r[1]} for r in rows]
        except Exception as e:
            logger.warning(f"[CuratorBrain] get_derived_from_source failed: {e}")
            return []
