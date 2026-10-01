"""The sync engine over one SQLite database (LB-12): ship, export, apply.

    ship(conn)            local changes (from `_changes`) → versioned row states
    export(conn, vv)      what a peer holding `vv` is missing, one page at a time
    apply(conn, page)     merge a peer's page in ONE transaction

Row states travel whole (every synced field with its clock and ancestry), so a
page is self-contained, delivery can repeat, and an entry that was coalesced
away is always covered by a newer one. Transport-agnostic: the harness calls
these directly across three in-process replicas; the TLS protocol calls the
same three functions.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.sync import journal, merge, registry
from services.sync.hlc import Clock

DATA_PREFIX = "@data/"


class Replica:
    """One Mac's view of one database: its connection, identity and clock.

    The connection must be the ENGINE's own, in autocommit mode, and it runs
    with foreign keys OFF: a remote apply must never trigger SQLite's native
    cascades, whose effects would be journaled as "remote" and never shipped —
    the two Macs would diverge. Orphans are resolved explicitly instead
    (`_resolve_orphans`), as ordinary local writes that ship like any other.
    """

    def __init__(self, conn: sqlite3.Connection, db: str, device: str, clock: Clock,
                 data_dir: Optional[Path] = None):
        self.conn, self.db, self.device, self.clock = conn, db, device, clock
        self.data_dir = Path(data_dir) if data_dir else None
        conn.execute("PRAGMA foreign_keys=OFF")


# ── helpers ─────────────────────────────────────────────────────────────────


def _state_get(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM _sync_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _state_set(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO _sync_state(key, value) VALUES (?, ?)", (key, str(value)))


def vv(conn) -> Dict[str, int]:
    return {o: s for o, s in conn.execute("SELECT origin, seq FROM _sync_vv")}


def _vv_raise(conn, origin: str, seq: int) -> None:
    conn.execute("""INSERT INTO _sync_vv(origin, seq) VALUES (?, ?)
                    ON CONFLICT(origin) DO UPDATE SET seq = MAX(seq, excluded.seq)""",
                 (origin, int(seq)))


def _next_oseq(r: Replica) -> int:
    cur = vv(r.conn).get(r.device, 0)
    _vv_raise(r.conn, r.device, cur + 1)
    return cur + 1


def _norm_path(r: Replica, value):
    if r.data_dir and isinstance(value, str):
        base = str(r.data_dir).rstrip("/") + "/"
        if value.startswith(base):
            return DATA_PREFIX + value[len(base):]
    return value


def _expand_path(r: Replica, value):
    if r.data_dir and isinstance(value, str) and value.startswith(DATA_PREFIX):
        return str(r.data_dir / value[len(DATA_PREFIX):])
    return value


def _where(t: registry.Table) -> str:
    return " AND ".join(f'"{c}" = ?' for c in t.pk)


def _read_row(r: Replica, t: registry.Table, pk: List[Any], cols: List[str]) -> Optional[Dict[str, Any]]:
    sel = ", ".join(f'"{c}"' for c in cols)
    row = r.conn.execute(f'SELECT {sel} FROM "{t.name}" WHERE {_where(t)}', pk).fetchone()
    if row is None:
        return None
    out = dict(zip(cols, row))
    for c in t.paths:
        if c in out:
            out[c] = _norm_path(r, out[c])
    return out


def _meta(r: Replica, tbl: str, pk: str) -> Dict[str, dict]:
    row = r.conn.execute("SELECT meta FROM _sync_rows WHERE tbl=? AND pk=?", (tbl, pk)).fetchone()
    return json.loads(row[0]) if row else {}


def _save_meta(r: Replica, tbl: str, pk: str, meta: Dict[str, dict]) -> None:
    r.conn.execute("INSERT OR REPLACE INTO _sync_rows(tbl, pk, meta) VALUES (?, ?, ?)",
                   (tbl, pk, json.dumps(meta, separators=(",", ":"))))


def _log(r: Replica, tbl: str, pk: str, origin: str, oseq: int) -> None:
    r.conn.execute("INSERT OR REPLACE INTO _sync_log(tbl, pk, origin, oseq) VALUES (?, ?, ?, ?)",
                   (tbl, pk, origin, oseq))


@contextmanager
def _txn(conn):
    """Join the caller's transaction, or open one: a ship must never leave a
    row's meta saved without its log entry."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ── ship ────────────────────────────────────────────────────────────────────


def _enrol(r: Replica) -> List[tuple]:
    """Rows that predate the triggers (or a table's first sync): version them once."""
    todo = []
    existing = {x[0] for x in r.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in registry.tables(r.db):
        if t.name not in existing or _state_get(r.conn, f"enrolled:{t.name}"):
            continue
        pkx = "json_array(" + ", ".join(f'"{c}"' for c in t.pk) + ")"
        for (pk,) in r.conn.execute(
                f'SELECT {pkx} FROM "{t.name}" WHERE {pkx} NOT IN '
                f"(SELECT pk FROM _sync_rows WHERE tbl = ?)", (t.name,)):
            todo.append((t.name, pk))
        _state_set(r.conn, f"enrolled:{t.name}", 1)
    return todo


def ship(r: Replica) -> int:
    """Turn local writes into versioned row states. Returns rows versioned.

    One clock per row per ship: every field that changed since the last ship
    gets it, with the previous clock pushed onto its ancestry. Changes produced
    by applying a remote page are skipped (`_remote_applied`).
    """
    with _txn(r.conn):
        return _ship(r)


def _ship(r: Replica) -> int:
    upto = int(_state_get(r.conn, "shipped_upto") or 0)
    rows = r.conn.execute(
        """SELECT seq, tbl, pk FROM _changes WHERE seq > ?
           AND seq NOT IN (SELECT seq FROM _remote_applied) ORDER BY seq""", (upto,)).fetchall()
    last = r.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM _changes").fetchone()[0]
    todo = list(dict.fromkeys([(tbl, pk) for _, tbl, pk in rows] + _enrol(r)))
    n = 0
    for tbl, pk in todo:
        try:
            t = registry.table(r.db, tbl)
        except KeyError:
            continue
        if _version_local(r, t, pk):
            n += 1
    _state_set(r.conn, "shipped_upto", last)
    # The journal is only needed until shipped; `_sync_log` is what peers read.
    r.conn.execute("DELETE FROM _changes WHERE seq <= ?", (last,))
    r.conn.execute("DELETE FROM _remote_applied WHERE seq <= ?", (last,))
    return n


def _version_local(r: Replica, t: registry.Table, pk: str) -> bool:
    cols = journal.synced_columns(r.conn, t)
    row = _read_row(r, t, json.loads(pk), cols)
    meta = _meta(r, t.name, pk)
    was_deleted = merge.is_deleted(meta)
    clock = None
    changed = False

    def bump(reg: Optional[dict]) -> dict:
        nonlocal clock
        clock = clock or r.clock.now()
        hist = [clock] + list((reg or {}).get("h", []))
        return {"c": clock, "h": hist[:merge.CHAIN]}

    if row is None:
        if not meta or was_deleted:
            return False
        meta[merge.DEL] = {"v": True, **bump(meta.get(merge.DEL))}
        changed = True
    else:
        resurrected = was_deleted
        if was_deleted or merge.DEL not in meta:
            meta[merge.DEL] = {"v": False, **bump(meta.get(merge.DEL))}
            changed = True
        for c, value in row.items():
            x = merge.vhash(value)
            reg = meta.get(c)
            # A row re-created after a delete is a fresh write of EVERY field:
            # each gets the new clock, so no register from the deleted copy
            # (which carries no value) can outrank a field of the live row.
            # Found by the 10k harness (NOT NULL on a resurrected note).
            if reg is None or reg.get("x") != x or resurrected:
                meta[c] = {**bump(reg), "x": x}
                changed = True
    if changed:
        _save_meta(r, t.name, pk, meta)
        _log(r, t.name, pk, r.device, _next_oseq(r))
    return changed


# ── export ──────────────────────────────────────────────────────────────────


def export(r: Replica, peer_vv: Dict[str, int], limit: int = 500) -> Dict[str, Any]:
    """The next page of row states a peer holding `peer_vv` does not have."""
    ship(r)
    entries = []
    for origin, oseq_max in r.conn.execute("SELECT origin, MAX(oseq) FROM _sync_log GROUP BY origin"):
        have = int(peer_vv.get(origin, 0))
        if oseq_max <= have:
            continue
        entries += r.conn.execute(
            "SELECT tbl, pk, origin, oseq FROM _sync_log WHERE origin=? AND oseq>? ORDER BY oseq",
            (origin, have)).fetchall()
    entries.sort(key=lambda e: (e[2], e[3]))
    page, more = entries[:limit], len(entries) > limit
    versions = []
    for tbl, pk, origin, oseq in page:
        t = registry.table(r.db, tbl)
        meta = _meta(r, tbl, pk)
        row = _read_row(r, t, json.loads(pk), journal.synced_columns(r.conn, t))
        state = {}
        for name, reg in meta.items():
            if name == merge.DEL:
                state[name] = {"v": reg["v"], "c": reg["c"], "h": reg["h"]}
            elif row is not None and name in row:
                state[name] = {"v": merge.encode(row[name]), "c": reg["c"], "h": reg["h"]}
            else:
                state[name] = {"v": None, "nv": True, "c": reg["c"], "h": reg["h"]}
        versions.append({"tbl": tbl, "pk": pk, "origin": origin, "oseq": oseq, "state": state})
    return {"versions": versions, "more": more, "remaining": len(entries),
            "vv": vv(r.conn) if not more else {}}


def pending(r: Replica, peer_vv: Dict[str, int]) -> int:
    """How many row versions a peer holding `peer_vv` still lacks — the denominator a
    progress bar needs before the first page moves."""
    ship(r)
    n = 0
    for origin, oseq_max in r.conn.execute("SELECT origin, MAX(oseq) FROM _sync_log GROUP BY origin"):
        have = int(peer_vv.get(origin, 0))
        if oseq_max > have:
            n += r.conn.execute("SELECT COUNT(*) FROM _sync_log WHERE origin=? AND oseq>?",
                                (origin, have)).fetchone()[0]
    return n


# ── apply ───────────────────────────────────────────────────────────────────


def _local_state(r: Replica, t: registry.Table, pk: str, cols: List[str]) -> Dict[str, dict]:
    meta = _meta(r, t.name, pk)
    row = _read_row(r, t, json.loads(pk), cols)
    state = {}
    for name, reg in meta.items():
        if name == merge.DEL:
            state[name] = {"v": reg["v"], "c": reg["c"], "h": reg["h"]}
        elif row is not None and name in row:
            state[name] = {"v": merge.encode(row[name]), "c": reg["c"], "h": reg["h"]}
        else:
            state[name] = {"v": None, "nv": True, "c": reg["c"], "h": reg["h"]}
    return state


def _same(a: Dict[str, dict], b: Dict[str, dict]) -> bool:
    if set(a) != set(b):
        return False
    return all(a[k]["c"] == b[k]["c"] for k in a)


def apply(r: Replica, page: Dict[str, Any], *, dry_run: bool = False) -> Dict[str, Any]:
    """Merge one page in ONE transaction. Dry run computes the same report and
    rolls back. Returns counts plus the conflicts raised."""
    report = {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0,
              "conflicts": [], "tables": {}}
    conn = r.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        ship(r)                                   # local writes get clocks before any merge
        before = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM _changes").fetchone()[0]
        order = {t.name: i for i, t in enumerate(registry.tables(r.db))}
        versions = sorted(page.get("versions", []), key=lambda v: order.get(v["tbl"], 999))
        max_seen: Dict[str, int] = {}
        for v in versions:
            r.clock.observe(max((reg["c"] for reg in v["state"].values()), default=None))
            max_seen[v["origin"]] = max(max_seen.get(v["origin"], 0), int(v["oseq"]))
            try:
                t = registry.table(r.db, v["tbl"])
            except KeyError:
                continue
            _apply_one(r, t, v, report)
        after = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM _changes").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO _remote_applied(seq) "
                     "SELECT seq FROM _changes WHERE seq > ? AND seq <= ?", (before, after))
        for origin, seq in max_seen.items():
            _vv_raise(conn, origin, seq)
        for origin, seq in (page.get("vv") or {}).items():
            _vv_raise(conn, origin, seq)          # the page was the last: all of it is here
        # AFTER `after`: these are this Mac's own writes and must ship.
        _resolve_orphans(r, report)
        if r.db == "main":
            for c in report["conflicts"]:
                _record_conflict(conn, c)
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return report


def _apply_one(r: Replica, t: registry.Table, v: Dict[str, Any], report: Dict[str, Any]) -> None:
    pk, remote = v["pk"], v["state"]
    cols = journal.synced_columns(r.conn, t)
    remote = {k: reg for k, reg in remote.items() if k == merge.DEL or k in cols}
    local = _local_state(r, t, pk, cols)
    merged, conflicts = merge.merge_row(t.name, pk, local, remote, t.content)
    report["conflicts"].extend(conflicts)
    stats = report["tables"].setdefault(t.name, {"in": 0, "changed": 0})
    stats["in"] += 1
    if local and _same(merged, local):
        report["unchanged"] += 1
        return

    pkvals = json.loads(pk)
    exists = r.conn.execute(f'SELECT 1 FROM "{t.name}" WHERE {_where(t)}', pkvals).fetchone()
    meta = {}
    if merge.is_deleted(merged):
        if exists:
            r.conn.execute(f'DELETE FROM "{t.name}" WHERE {_where(t)}', pkvals)
            report["deleted"] += 1
            _note_removed(report, t.name, pkvals[0])
        for name, reg in merged.items():
            meta[name] = {k: reg[k] for k in ("c", "h")} | ({"v": reg["v"]} if name == merge.DEL else {})
            if name != merge.DEL and not reg.get("nv"):
                meta[name]["x"] = merge.vhash(merge.decode(reg["v"]))
    else:
        values = {}
        for name, reg in merged.items():
            if name == merge.DEL:
                meta[name] = {"v": reg["v"], "c": reg["c"], "h": reg["h"]}
                continue
            if reg.get("nv"):
                # A register whose value only a deleted side knew: keep ours.
                lv = local.get(name)
                value = merge.decode(lv["v"]) if lv and not lv.get("nv") else None
            else:
                value = merge.decode(reg["v"])
            values[name] = value
            meta[name] = {"c": reg["c"], "h": reg["h"], "x": merge.vhash(value)}
        for c in t.pk:
            values.setdefault(c, pkvals[t.pk.index(c)])
        for c in t.paths:
            if c in values:
                values[c] = _expand_path(r, values[c])
        names = list(values)
        cols_sql = ", ".join(f'"{c}"' for c in names)
        qs = ", ".join("?" for _ in names)
        updates = ", ".join(f'"{c}" = excluded."{c}"' for c in names if c not in t.pk)
        conflict_target = ", ".join(f'"{c}"' for c in t.pk)
        sql = f'INSERT INTO "{t.name}" ({cols_sql}) VALUES ({qs}) ON CONFLICT({conflict_target}) '
        sql += f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
        r.conn.execute(sql, [values[c] for c in names])
        report["updated" if exists else "inserted"] += 1
        # A source whose text changed here must be re-embedded on this Mac — each Mac
        # keeps its own search index (12j); a new one is found by the indexer's diff.
        if t.name == "sources" and exists and "content" in values:
            lv = local.get("content")
            if not lv or lv.get("nv") or merge.decode(lv["v"]) != values["content"]:
                report.setdefault("reindex", []).append(pkvals[0])
    stats["changed"] += 1
    _save_meta(r, t.name, pk, meta)
    if _same(merged, remote):
        _log(r, t.name, pk, v["origin"], int(v["oseq"]))     # relay the remote write as-is
    else:
        _log(r, t.name, pk, r.device, _next_oseq(r))          # a merge: a new state to share


def _resolve_orphans(r: Replica, report: Dict[str, Any]) -> None:
    """Rows whose parent another Mac deleted, handled as the schema declares.

    Only a parent that is KNOWN deleted (a tombstone in `_sync_rows`) counts: a
    parent that simply has not arrived yet (it is on a later page) leaves the
    child alone. `SET NULL` unfiles the child; `CASCADE` deletes it — and a
    child with content (a note, a source) is kept in a conflict item first, so a
    note added to a notebook someone else deleted is not silently lost. The item
    id comes from the child's own clocks, so every Mac that resolves the same
    orphan records the same item.
    """
    conn = r.conn
    synced = {t.name: t for t in registry.tables(r.db)}
    for _ in range(8):                                   # cascades nest; bounded
        acted = False
        for child, rowid, parent, fkid in conn.execute("PRAGMA foreign_key_check").fetchall():
            t = synced.get(child)
            if t is None or parent not in synced:
                continue
            fk = [f for f in conn.execute(f'PRAGMA foreign_key_list("{child}")') if f[0] == fkid]
            if not fk:
                continue
            _, _, _, col, to, _, on_delete, _ = fk[0]
            pkcols = ", ".join(f'"{c}"' for c in t.pk)
            row = conn.execute(f'SELECT {pkcols}, "{col}" FROM "{child}" WHERE rowid=?', (rowid,)).fetchone()
            if row is None or row[-1] is None:
                continue
            ptab = synced[parent]
            if list(ptab.pk) != [to or ptab.pk[0]]:
                continue
            if not merge.is_deleted(_meta(r, parent, json.dumps([row[-1]]))):
                continue                                 # parent not arrived yet, not deleted
            pk = json.dumps(list(row[:-1]))
            if on_delete == "SET NULL":
                conn.execute(f'UPDATE "{child}" SET "{col}" = NULL WHERE rowid=?', (rowid,))
            else:
                if t.content:
                    meta = _meta(r, child, pk)
                    clocks = [reg["c"] for k, reg in meta.items() if k != merge.DEL]
                    vals = _read_row(r, t, list(row[:-1]), list(t.content)) or {}
                    report["conflicts"].append({
                        "id": merge.conflict_id(child, pk, "__orphan__", max(clocks, default=""), str(row[-1])),
                        "tbl": child, "pk": pk, "field": "__orphan__", "kind": "orphaned",
                        "kept_value": None, "other_value": {k: merge.encode(v) for k, v in vals.items()},
                        "kept_clock": None, "other_clock": max(clocks, default=None),
                    })
                conn.execute(f'DELETE FROM "{child}" WHERE rowid=?', (rowid,))
                _note_removed(report, child, row[0])
            acted = True
        if not acted:
            return


# What a delete leaves outside the database, by table → the report key the receiving
# Mac's post-apply cleanup reads (peer._after_apply): search entries, media files, and
# a deleted notebook's own folder / vector table / derived stores.
_REMOVED_KEYS = {"sources": "unindex", "audio_generations": "removed_audio",
                 "video_generations": "removed_video", "notebooks": "removed_notebooks"}


def _note_removed(report: Dict[str, Any], table: str, key: Any) -> None:
    k = _REMOVED_KEYS.get(table)
    if k:
        report.setdefault(k, []).append(key)


def _record_conflict(conn, c: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO sync_conflicts
           (id, tbl, pk, field, kind, kept_value, other_value, kept_clock, other_clock, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', CURRENT_TIMESTAMP)""",
        (c["id"], c["tbl"], c["pk"], c["field"], c.get("kind", "concurrent-edit"),
         json.dumps(c["kept_value"]), json.dumps(c["other_value"]),
         c["kept_clock"], c["other_clock"]))
