"""A fresh install's startup must leave localbook.db writable.

2026-10-01: a one-time startup migration ran an UPDATE on the event-loop thread's
shared connection and never committed. On a FRESH data dir (a new Mac's first
launch) that implicit transaction write-locked the database for every other
connection — sync, the sync engine, every worker thread got "database is locked"
— until something else on that thread happened to commit. Existing installs never
saw it (the migration's sentinel file had long been written).

This runs the real app lifespan in a subprocess against a throwaway data dir and
probes for a write lock; on failure it names the connection holding a transaction
and its last statements.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

HARNESS = textwrap.dedent('''
    import asyncio, collections, sqlite3, sys, threading, traceback, weakref
    _real = sqlite3.connect
    CONNS = []
    class Traced(sqlite3.Connection):      # plain Connections cannot be weak-referenced
        pass
    def connect(*a, **k):
        k.setdefault("factory", Traced)
        c = _real(*a, **k)
        hist = collections.deque(maxlen=6)
        c.set_trace_callback(lambda sql: hist.append(sql[:160]))
        CONNS.append((weakref.ref(c), str(a[0]), hist))
        return c
    sqlite3.connect = connect
    from config import settings
    import main
    def writable():
        c = _real(str(settings.data_dir / "localbook.db"), timeout=0.5)
        try:
            c.execute("BEGIN IMMEDIATE"); c.execute("ROLLBACK"); return True
        except sqlite3.OperationalError:
            return False
        finally:
            c.close()
    async def run():
        async with main.app.router.lifespan_context(main.app):
            for _ in range(SECONDS):
                await asyncio.sleep(1)
                if not writable():
                    for ref, path, hist in CONNS:
                        c = ref()
                        if c is not None and c.in_transaction and path.endswith("localbook.db"):
                            print("HOLDER", list(hist))
                    print("LOCKED"); return
        print("WRITABLE")
    asyncio.run(run())
''')


def test_fresh_startup_does_not_hold_a_write_lock(tmp_path):
    script = tmp_path / "h.py"
    script.write_text("SECONDS = 30\n" + HARNESS)
    env = dict(os.environ, PYTHONPATH=str(BACKEND), LOCALBOOK_NO_INTERACTIVE_AUTH="1", LOCALBOOK_DATA_DIR=str(tmp_path / "LocalBook"),
               API_PORT="8793", SYNC_PORT="47830", HF_HUB_OFFLINE="1", MAIN_MODEL="lb-test/none",
               FAST_MODEL="lb-test/none", EMBEDDING_MODEL="lb-test/none", IMAGE_MODEL="lb-test/none")
    out = subprocess.run([sys.executable, str(script)], cwd=str(BACKEND), env=env,
                         capture_output=True, text=True, timeout=240)
    lines = [l for l in out.stdout.splitlines() if l.startswith(("HOLDER", "LOCKED", "WRITABLE"))]
    assert "WRITABLE" in lines, "\n".join(lines) or out.stdout[-2000:] + out.stderr[-2000:]
