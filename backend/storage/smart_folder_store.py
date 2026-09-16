"""Smart Folder state — the review queue and the routing rules.

Two tables, one writer.

`folder_pending` holds files a Smart Folder has seen and analysed but NOT
ingested. Nothing here belongs to a notebook yet.

`routing_rules` holds the user's explicit standing authorisations: "recordings
with Sarah Chen go to the Sarah 1:1 notebook." A rule is the ONLY thing that
lets a file route without a click. Confidence never authorises anything —
that separation is the safety model, not an implementation detail.

Matching is deliberately literal. A rule scoped to participants matches when
ALL of its named participants are present; a rule scoped to topics matches when
ANY topic overlaps. A rule with both scopes must satisfy both. Nothing fuzzy,
nothing learned, nothing that can drift — the user must be able to read a rule
and know exactly what it will do.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _norm(name: str) -> str:
    """Compare people case- and whitespace-insensitively, nothing cleverer."""
    return " ".join(str(name or "").strip().lower().split())


class SmartFolderStore:
    def _db(self):
        from storage.database import get_db
        return get_db().get_connection()

    # ── rules ────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_rule(row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("scope_participants", "scope_topics"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except Exception:
                d[k] = []
        d["enabled"] = bool(d.get("enabled"))
        return d

    def list_rules(self, notebook_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._db()
        if notebook_id:
            rows = conn.execute(
                "SELECT * FROM routing_rules WHERE notebook_id = ? ORDER BY created_at DESC",
                (notebook_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM routing_rules ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def create_rule(self, *, notebook_id: str,
                    participants: Optional[List[str]] = None,
                    topics: Optional[List[str]] = None,
                    created_from: Optional[str] = None) -> Dict[str, Any]:
        participants = [p for p in (participants or []) if str(p).strip()]
        topics = [t for t in (topics or []) if str(t).strip()]
        if not participants and not topics:
            raise ValueError(
                "A rule with no scope would route every recording. Name at least "
                "one participant or topic."
            )
        rid = str(uuid.uuid4())
        conn = self._db()
        conn.execute(
            """INSERT INTO routing_rules
                 (id, scope_participants, scope_topics, notebook_id, created_at, created_from)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rid, json.dumps([_norm(p) for p in participants]),
             json.dumps([_norm(t) for t in topics]),
             notebook_id, datetime.utcnow().isoformat(), created_from),
        )
        conn.commit()
        logger.info(f"[smart-folder] rule created → {notebook_id} "
                    f"(participants={participants}, topics={topics})")
        return self.get_rule(rid)

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        row = self._db().execute(
            "SELECT * FROM routing_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return self._row_to_rule(row) if row else None

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        conn = self._db()
        conn.execute("UPDATE routing_rules SET enabled = ? WHERE id = ?",
                     (1 if enabled else 0, rule_id))
        conn.commit()
        return self.get_rule(rule_id)

    def delete_rule(self, rule_id: str) -> bool:
        conn = self._db()
        cur = conn.execute("DELETE FROM routing_rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cur.rowcount > 0

    def record_rule_hit(self, rule_id: str) -> None:
        conn = self._db()
        conn.execute(
            "UPDATE routing_rules SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), rule_id),
        )
        conn.commit()

    def match_rule(self, participants: List[str],
                   topics: List[str]) -> Optional[Dict[str, Any]]:
        """The only thing that may authorise an automatic route.

        Narrower rules win: a rule scoped to a person AND a topic beats one
        scoped to the person alone, so the specific authorisation the user gave
        takes precedence over the broad one.
        """
        have_p = {_norm(p) for p in participants or []}
        have_t = {_norm(t) for t in topics or []}
        best = None
        for rule in self.list_rules():
            if not rule["enabled"]:
                continue
            want_p = set(rule["scope_participants"])
            want_t = set(rule["scope_topics"])
            if want_p and not want_p.issubset(have_p):
                continue
            if want_t and not (want_t & have_t):
                continue
            specificity = len(want_p) + len(want_t)
            if best is None or specificity > best[0]:
                best = (specificity, rule)
        return best[1] if best else None

    # ── pending queue ────────────────────────────────────────────────────
    @staticmethod
    def _row_to_pending(row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("participants", "topics", "alternatives"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except Exception:
                d[k] = []
        return d

    def list_pending(self, *, link_id: Optional[str] = None,
                     notebook_id: Optional[str] = None,
                     status: str = "pending") -> List[Dict[str, Any]]:
        sql = "SELECT * FROM folder_pending WHERE status = ?"
        args: List[Any] = [status]
        if link_id:
            sql += " AND link_id = ?"
            args.append(link_id)
        if notebook_id:
            # Items SUGGESTED for this notebook, so approval can happen where
            # the content would actually land.
            sql += " AND suggested_id = ?"
            args.append(notebook_id)
        sql += " ORDER BY created_at DESC"
        return [self._row_to_pending(r) for r in self._db().execute(sql, args).fetchall()]

    def count_pending(self) -> int:
        row = self._db().execute(
            "SELECT COUNT(*) AS n FROM folder_pending WHERE status = 'pending'"
        ).fetchone()
        return int(row["n"] or 0)

    def get_pending(self, item_id: str) -> Optional[Dict[str, Any]]:
        row = self._db().execute(
            "SELECT * FROM folder_pending WHERE id = ?", (item_id,)
        ).fetchone()
        return self._row_to_pending(row) if row else None

    def upsert_pending(self, *, link_id: str, abs_path: str, filename: str,
                       size: int, mtime: float, content_hash: Optional[str],
                       participants: List[str], topics: List[str], summary: str,
                       suggested_id: Optional[str], suggested_name: Optional[str],
                       confidence: float,
                       alternatives: List[Dict[str, Any]]) -> Dict[str, Any]:
        existing = self._db().execute(
            "SELECT id FROM folder_pending WHERE link_id = ? AND abs_path = ?",
            (link_id, abs_path),
        ).fetchone()
        item_id = existing["id"] if existing else str(uuid.uuid4())
        conn = self._db()
        conn.execute(
            """INSERT INTO folder_pending
                 (id, link_id, abs_path, filename, content_hash, size, mtime,
                  participants, topics, summary, suggested_id, suggested_name,
                  confidence, alternatives, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)
               ON CONFLICT(link_id, abs_path) DO UPDATE SET
                 participants = excluded.participants,
                 topics = excluded.topics,
                 summary = excluded.summary,
                 suggested_id = excluded.suggested_id,
                 suggested_name = excluded.suggested_name,
                 confidence = excluded.confidence,
                 alternatives = excluded.alternatives,
                 content_hash = excluded.content_hash,
                 mtime = excluded.mtime,
                 size = excluded.size""",
            (item_id, link_id, abs_path, filename, content_hash, int(size), float(mtime),
             json.dumps(participants), json.dumps(topics), summary or "",
             suggested_id, suggested_name, float(confidence),
             json.dumps(alternatives), datetime.utcnow().isoformat()),
        )
        conn.commit()
        return self.get_pending(item_id)

    def resolve_pending(self, item_id: str, *, status: str,
                        notebook_id: Optional[str] = None,
                        error: Optional[str] = None) -> None:
        conn = self._db()
        conn.execute(
            """UPDATE folder_pending
               SET status = ?, resolved_at = ?, resolved_to = ?, error = ?
               WHERE id = ?""",
            (status, datetime.utcnow().isoformat(), notebook_id, error, item_id),
        )
        conn.commit()

    def forget_link(self, link_id: str) -> None:
        conn = self._db()
        conn.execute("DELETE FROM folder_pending WHERE link_id = ?", (link_id,))
        conn.commit()

    def unrouted_by_participant(self, minimum: int = 3) -> List[Dict[str, Any]]:
        """People who keep turning up with nowhere to file them.

        Repeated no-match for the same person is the system noticing a
        relationship the user has not made a notebook for yet.
        """
        counts: Dict[str, int] = {}
        for item in self.list_pending():
            if item.get("suggested_id"):
                continue
            for p in item.get("participants") or []:
                counts[p] = counts.get(p, 0) + 1
        return [{"participant": p, "count": c}
                for p, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c >= minimum]


smart_folder_store = SmartFolderStore()
