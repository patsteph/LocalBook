"""Keep sync's bookkeeping from growing forever (LB-12).

Most of the journal is already bounded: `_changes` and `_remote_applied` drain
on every ship, and `_sync_log` holds one entry per row. What grew without end:

  * **Tombstones** — a deleted row keeps its meta (`_sync_rows`) and log entry
    forever, so a Mac that was away can still learn of the delete.
  * **Synced log tables** — activity, correspondent, routing and voice events,
    and Curator's brain events.
  * **Resolved conflicts.**

Rules, each chosen so that no Mac can be left behind:

  * A tombstone is dropped only once EVERY paired, non-revoked Mac has
    acknowledged it — its version vector covers the delete's (origin, oseq) —
    and it is older than TOMBSTONE_DAYS. A Mac that never comes back holds
    tombstones until it returns or is revoked: never a resurrected row.
  * Log rows past their age are purged LOCALLY on every Mac by the same rule:
    rows, their meta and log entries go in one transaction and the delete is
    not shipped. Every Mac converges because every Mac applies the same cutoff.
  * Resolved conflicts older than CONFLICT_DAYS are deleted normally (shipped).

Peers' version vectors are recorded whenever one is seen (hello, pull, push).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

from services.sync import engine, hlc, merge, runtime, store

logger = logging.getLogger(__name__)

SCHEDULE_ID = "sync-retention"
DEFAULT_INTERVAL = 24 * 3600
TOMBSTONE_DAYS = 30
CONFLICT_DAYS = 90
STALE_PEER_DAYS = 30
# db → table → (timestamp column, days kept). `unsubscribe_log` is an audit trail: never.
LOG_RETENTION = {
    "main": {"activity_events": ("ts", 180), "correspondent_events": ("ts", 180),
             "routing_decisions": ("ts", 180), "voice_observations": ("created_at", 180)},
    "brain": {"events": ("ts", 30)},
}


# ── what each peer has ───────────────────────────────────────────────────────


def record_acked(device_id: str, vvs: Dict[str, Dict[str, int]]) -> None:
    """Remember the highest version of each origin a peer is known to hold."""
    for db, vv in (vvs or {}).items():
        if not isinstance(vv, dict):
            continue
        key = f"acked_vv:{device_id}:{db}"
        cur = store.get(key, {}) or {}
        changed = False
        for origin, seq in vv.items():
            if int(seq) > int(cur.get(origin, 0)):
                cur[origin] = int(seq)
                changed = True
        if changed:
            store.put(key, cur)


def _peers_acked(db: str) -> List[Dict[str, int]]:
    return [store.get(f"acked_vv:{d['device_id']}:{db}", {}) or {} for d in store.devices()]


# ── tombstones ───────────────────────────────────────────────────────────────


def gc_tombstones(r: engine.Replica, acked: List[Dict[str, int]], now_ms: int,
                  days: int = TOMBSTONE_DAYS) -> int:
    """Drop tombstones every peer holds and that are old enough. Returns the count."""
    cutoff = now_ms - days * 86400 * 1000
    gone = []
    for tbl, pk, meta, origin, oseq in r.conn.execute(
            "SELECT s.tbl, s.pk, s.meta, l.origin, l.oseq FROM _sync_rows s "
            "JOIN _sync_log l ON l.tbl = s.tbl AND l.pk = s.pk"):
        reg = (json.loads(meta) or {}).get(merge.DEL)
        if not reg or not reg.get("v"):
            continue
        try:
            wall = hlc._parse(reg["c"])[0]
        except Exception:
            continue
        if wall > cutoff:
            continue
        if all(int(a.get(origin, 0)) >= int(oseq) for a in acked):
            gone.append((tbl, pk))
    with engine._txn(r.conn):
        for tbl, pk in gone:
            r.conn.execute("DELETE FROM _sync_rows WHERE tbl=? AND pk=?", (tbl, pk))
            r.conn.execute("DELETE FROM _sync_log WHERE tbl=? AND pk=?", (tbl, pk))
    return len(gone)


# ── log tables ───────────────────────────────────────────────────────────────


def purge_old_logs(r: engine.Replica, tables: Dict[str, Any], now: datetime) -> Dict[str, int]:
    """Purge old log rows on this Mac only — nothing ships (every Mac does the same)."""
    out: Dict[str, int] = {}
    existing = {x[0] for x in r.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, (col, days) in tables.items():
        if table not in existing:
            continue
        cutoff = (now - timedelta(days=days)).isoformat()
        with engine._txn(r.conn):
            r.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _purge(pk TEXT PRIMARY KEY)")
            r.conn.execute("DELETE FROM _purge")
            r.conn.execute(f'INSERT OR IGNORE INTO _purge SELECT json_array("uid") FROM "{table}" '
                           f'WHERE "{col}" IS NOT NULL AND "{col}" != \'\' AND "{col}" < ?', (cutoff,))
            n = r.conn.execute("SELECT COUNT(*) FROM _purge").fetchone()[0]
            if not n:
                continue
            before = r.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM _changes").fetchone()[0]
            r.conn.execute(f'DELETE FROM "{table}" WHERE json_array("uid") IN (SELECT pk FROM _purge)')
            # The delete triggers journaled these; mark them applied so ship never sends them.
            r.conn.execute("INSERT OR IGNORE INTO _remote_applied(seq) SELECT seq FROM _changes WHERE seq > ?",
                           (before,))
            for jt in ("_sync_rows", "_sync_log"):
                r.conn.execute(f"DELETE FROM {jt} WHERE tbl=? AND pk IN (SELECT pk FROM _purge)", (table,))
            out[table] = n
    return out


def purge_resolved_conflicts(days: int = CONFLICT_DAYS) -> int:
    """Old resolved conflict items — deleted through the app's connection, so it ships."""
    from storage.database import get_db

    conn = get_db().get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("DELETE FROM sync_conflicts WHERE status='resolved' AND resolved_at IS NOT NULL "
                       "AND resolved_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount or 0


# ── the daily run ────────────────────────────────────────────────────────────


def run(now_ms: int = None) -> Dict[str, Any]:
    now_ms = now_ms or int(time.time() * 1000)
    now = datetime.utcfromtimestamp(now_ms / 1000)
    report: Dict[str, Any] = {"at": now_ms / 1000, "tombstones": {}, "logs": {}, "conflicts": 0}
    for db in runtime.DB_FILES:
        if not (runtime.data_dir() / runtime.DB_FILES[db]).exists():
            continue
        with runtime.engine_lock:
            r = runtime.replica(db)
            try:
                engine.ship(r)                       # pending local changes first
                report["tombstones"][db] = gc_tombstones(r, _peers_acked(db), now_ms)
                if db in LOG_RETENTION:
                    report["logs"].update(purge_old_logs(r, LOG_RETENTION[db], now))
            finally:
                r.conn.close()
    try:
        report["conflicts"] = purge_resolved_conflicts()
    except Exception as exc:
        logger.warning("[sync-retention] conflicts: %s", exc)
    store.put("retention_last", report)
    logger.info("[sync-retention] %s", report)
    return report


def due() -> bool:
    try:
        from services.schedule_store import schedule_store
        if not schedule_store.is_enabled(SCHEDULE_ID):
            return False
        interval = schedule_store.get_interval(SCHEDULE_ID, DEFAULT_INTERVAL)
    except Exception:
        interval = DEFAULT_INTERVAL
    last = (store.get("retention_last") or {}).get("at", 0)
    return time.time() - float(last) >= max(3600, int(interval or DEFAULT_INTERVAL))


def stale_peers(days: int = STALE_PEER_DAYS) -> List[Dict[str, Any]]:
    """Paired Macs that haven't synced lately — they hold deletes until they return."""
    cut = time.time() - days * 86400
    return [{"device_id": d["device_id"], "name": d.get("name"),
             "days": int((time.time() - float(d.get("last_seen") or 0)) / 86400) if d.get("last_seen") else None}
            for d in store.devices() if not d.get("last_seen") or float(d["last_seen"]) < cut]
