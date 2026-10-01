"""The backend stops when the app that started it is gone (Force Quit / crash)."""
import os
import subprocess
import sys
import textwrap
import time

from utils import parent_watch


def test_no_parent_pid_means_no_watch(monkeypatch):
    monkeypatch.delenv("LOCALBOOK_PARENT_PID", raising=False)
    assert parent_watch.start() is False


def test_app_gone_detects_reparenting_and_a_dead_pid():
    assert parent_watch.app_gone(os.getppid()) is False
    assert parent_watch.app_gone(os.getppid() + 999_999) is True


def test_an_orphaned_child_terminates_itself(tmp_path):
    """A child watching a parent that exits gets SIGTERM within a few seconds."""
    backend = tmp_path / "child.py"
    backend.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(os.path.dirname(os.path.dirname(parent_watch.__file__)))!r})
        from utils import parent_watch
        parent_watch.INTERVAL = 0.2
        parent_watch.CONFIRMS = 3
        parent_watch.start()
        open({str(tmp_path / 'started')!r}, 'w').close()
        time.sleep(30)
    """))
    # the "app": starts the child with its own pid, then exits immediately
    app = tmp_path / "app.py"
    app.write_text(textwrap.dedent(f"""
        import os, subprocess, sys
        p = subprocess.Popen([sys.executable, {str(backend)!r}],
                             env=dict(os.environ, LOCALBOOK_PARENT_PID=str(os.getpid())))
        print(p.pid, flush=True)
        import time
        while not os.path.exists({str(tmp_path / 'started')!r}):
            time.sleep(0.05)
    """))
    out = subprocess.run([sys.executable, str(app)], capture_output=True, text=True, timeout=30)
    child = int(out.stdout.strip())
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(child, 9)
    raise AssertionError("the orphaned child kept running")


def test_one_odd_reading_does_not_stop_the_backend(monkeypatch):
    """Acting needs CONFIRMS misses in a row; a single miss resets on the next good check."""
    seq = iter([True, False, True, False, True, True, True])
    monkeypatch.setattr(parent_watch, "app_gone", lambda pid: next(seq))
    monkeypatch.setattr(parent_watch, "INTERVAL", 0.01)
    killed = []
    monkeypatch.setattr(parent_watch.os, "kill", lambda pid, sig: killed.append(sig))
    monkeypatch.setenv("LOCALBOOK_PARENT_PID", "12345")
    parent_watch.start()
    time.sleep(0.5)
    assert len(killed) == 1        # only after the final three misses in a row
