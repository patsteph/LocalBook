"""In-process replicas for the LB-12 sync engine (12m).

Each replica is a REAL localbook.db — built by the app's own schema code plus
migration 0002 — with the real triggers, on its own file. Sync goes through the
same `export` / `apply` functions the TLS protocol calls, so what the harness
proves is what ships. Built once per session as a template and copied per
replica, because building the full schema per example would make a 10,000
scenario run take hours.
"""

from __future__ import annotations

import importlib
import itertools
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List

from services.sync import engine, journal, registry
from services.sync.hlc import Clock

_TEMPLATE: Dict[str, Path] = {}


def template(tmp_root: Path) -> Path:
    """localbook.db with the full app schema + migrations, built once."""
    if "main" in _TEMPLATE and _TEMPLATE["main"].exists():
        return _TEMPLATE["main"]
    from config import settings

    d = tmp_root / "template"
    d.mkdir(parents=True, exist_ok=True)
    saved = settings.data_dir
    settings.data_dir = d
    try:
        import storage.database as db
        importlib.reload(db)
        conn = db.Database().get_connection()
        from services import migration_ledger
        migration_ledger.run_pending(conn, d)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    finally:
        settings.data_dir = saved
        import storage.database as db
        importlib.reload(db)
    _TEMPLATE["main"] = d / "localbook.db"
    return _TEMPLATE["main"]


class Mac:
    """One replica: a data dir, a connection for 'the app' and the engine."""

    _ticks = itertools.count(1)

    def __init__(self, root: Path, name: str, tmpl: Path, clock_ms=None):
        self.name = name
        self.dir = root / name
        self.dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmpl, self.dir / "localbook.db")
        db_path = str(self.dir / "localbook.db")
        # "The app" writes with foreign keys ON (its cascades are real local
        # changes); the engine gets its own connection, as in production.
        conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        journal.install(conn, "main")
        self.conn = conn
        self.engine_conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        # A deterministic, shared-ish wall clock so HLC ordering is reproducible
        # inside one test, while still exercising the counter path.
        self.clock = Clock(name, now_ms=clock_ms or (lambda: 1_700_000_000_000 + next(Mac._ticks)))
        self.rep = engine.Replica(self.engine_conn, "main", name, self.clock, data_dir=self.dir)

    # ── "the app" ──────────────────────────────────────────────────────────
    def sql(self, q: str, *args):
        return self.conn.execute(q, args)

    def notebook(self, nid: str, title: str = "NB"):
        self.sql("INSERT INTO notebooks (id, title, created_at, updated_at) VALUES (?, ?, 'now', 'now')",
                 nid, title)

    def note(self, nid: str, notebook_id: str, title: str = "t", body: str = "b"):
        self.sql("INSERT INTO canvas_notes (id, notebook_id, title, content_markdown, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, 'now', 'now')", nid, notebook_id, title, body)

    def source(self, sid: str, notebook_id: str, content: str = "text"):
        self.sql("INSERT INTO sources (id, notebook_id, filename, content, created_at) "
                 "VALUES (?, ?, 'f.txt', ?, 'now')", sid, notebook_id, content)

    # ── observation ────────────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, List[tuple]]:
        out = {}
        for t in registry.tables("main"):
            if t.name == "sync_conflicts":
                continue
            cols = journal.synced_columns(self.conn, t)
            if not cols:
                continue
            sel = ", ".join(f'"{c}"' for c in cols)
            out[t.name] = sorted(self.conn.execute(f'SELECT {sel} FROM "{t.name}"').fetchall(),
                                 key=repr)
        return out

    def conflicts(self) -> List[tuple]:
        return sorted(self.conn.execute(
            "SELECT id, tbl, pk, field, kept_value, other_value FROM sync_conflicts").fetchall())


def pull(dst: Mac, src: Mac, page_size: int = 7, dry_run: bool = False) -> Dict:
    """dst pulls everything it is missing from src, page by page."""
    total = {"inserted": 0, "updated": 0, "deleted": 0, "conflicts": []}
    for _ in range(10_000):
        page = engine.export(src.rep, engine.vv(dst.conn), limit=page_size)
        rep = engine.apply(dst.rep, page, dry_run=dry_run)
        for k in ("inserted", "updated", "deleted"):
            total[k] += rep[k]
        total["conflicts"] += rep["conflicts"]
        if not page["more"] or dry_run:
            return total
    raise AssertionError("pull did not terminate")


def sync(a: Mac, b: Mac, page_size: int = 7) -> None:
    """One contact, initiated by `a`: pull, then push (so an outbound-only Mac
    converges both ways)."""
    pull(a, b, page_size)
    pull(b, a, page_size)


def mesh(macs: List[Mac], rounds: int = 3) -> None:
    for _ in range(rounds):
        for a, b in itertools.permutations(macs, 2):
            sync(a, b)


def assert_converged(macs: List[Mac]) -> None:
    snaps = [m.snapshot() for m in macs]
    for m, s in zip(macs[1:], snaps[1:]):
        for tbl in snaps[0]:
            assert s[tbl] == snaps[0][tbl], f"{m.name} differs from {macs[0].name} in {tbl}"
    conf = [m.conflicts() for m in macs]
    for m, c in zip(macs[1:], conf[1:]):
        assert c == conf[0], f"{m.name} has different conflict items"
