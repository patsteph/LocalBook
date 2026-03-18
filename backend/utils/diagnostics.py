"""
Backend Diagnostics — Crash snapshot, heartbeat logging, and signal handlers.

Layer 1: Signal handler captures crash snapshot on SIGTERM/SIGABRT.
Layer 2: Heartbeat task logs RSS + active tasks + last endpoint every 30s.

Log files are written to {data_dir}/diagnostics.log with auto-rotation
(max 500KB per file, keep 2 backups).
"""
import asyncio
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from logging import getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import psutil

from config import settings

# ─── Diagnostics Logger (separate from app logging) ──────────────────────────

_diag_logger = None
_heartbeat_task: Optional[asyncio.Task] = None
_last_endpoint: str = ""
_last_endpoint_time: float = 0.0

HEARTBEAT_INTERVAL = 30  # seconds
MAX_LOG_BYTES = 500 * 1024  # 500KB per file
BACKUP_COUNT = 2


def _get_log_path() -> Path:
    return Path(settings.data_dir) / "diagnostics.log"


def _get_logger():
    """Lazy-init the rotating file logger."""
    global _diag_logger
    if _diag_logger is not None:
        return _diag_logger

    log_path = _get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    _diag_logger = getLogger("localbook.diagnostics")
    _diag_logger.setLevel(10)  # DEBUG
    _diag_logger.propagate = False

    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        __import__("logging").Formatter("%(message)s")
    )
    _diag_logger.addHandler(handler)
    return _diag_logger


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _memory_info() -> dict:
    """Current process memory stats."""
    try:
        proc = psutil.Process()
        mem = proc.memory_info()
        vm = psutil.virtual_memory()
        return {
            "rss_mb": round(mem.rss / (1024 * 1024), 1),
            "vms_mb": round(mem.vms / (1024 * 1024), 1),
            "system_available_gb": round(vm.available / (1024 ** 3), 2),
            "system_percent_used": vm.percent,
        }
    except Exception as e:
        return {"error": str(e)}


def _active_tasks_summary() -> list:
    """Names of currently running asyncio tasks."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            tasks = asyncio.all_tasks(loop)
            return [t.get_name() for t in tasks if not t.done()]
    except Exception:
        pass
    return []


# ─── Layer 1: Signal Handler — crash snapshot ────────────────────────────────

def _write_crash_snapshot(sig_name: str):
    """Write crash context to diagnostics log on signal receipt."""
    try:
        log = _get_logger()
        log.critical(
            f"\n{'='*60}\n"
            f"CRASH SNAPSHOT — Signal: {sig_name}\n"
            f"Time: {_now_iso()}\n"
            f"Memory: {_memory_info()}\n"
            f"Active tasks: {_active_tasks_summary()}\n"
            f"Last endpoint: {_last_endpoint} (at {_last_endpoint_time})\n"
            f"{'='*60}"
        )
        # Force flush
        for h in log.handlers:
            h.flush()
    except Exception:
        # Last resort — write directly
        try:
            with open(str(_get_log_path()), "a") as f:
                f.write(f"\n[CRASH] Signal={sig_name} time={_now_iso()} "
                        f"last_endpoint={_last_endpoint}\n")
                f.flush()
        except Exception:
            pass


def _signal_handler(signum, frame):
    """Handle SIGTERM/SIGABRT — write snapshot then re-raise."""
    sig_name = signal.Signals(signum).name
    print(f"[Diagnostics] Received {sig_name} — writing crash snapshot...")
    _write_crash_snapshot(sig_name)
    # Re-raise with default handler so the process actually exits
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def install_signal_handlers():
    """Install signal handlers for SIGTERM and SIGABRT.

    Call this once at startup (before uvicorn takes over).
    SIGINT is left to uvicorn's default handler.
    """
    for sig in (signal.SIGTERM, signal.SIGABRT):
        try:
            signal.signal(sig, _signal_handler)
        except (OSError, ValueError):
            # Some signals can't be caught in certain contexts
            pass
    print("[Diagnostics] Signal handlers installed (SIGTERM, SIGABRT)")


# ─── Layer 2: Heartbeat Logger ───────────────────────────────────────────────

def record_endpoint(endpoint: str):
    """Record the last endpoint hit. Call from middleware."""
    global _last_endpoint, _last_endpoint_time
    _last_endpoint = endpoint
    _last_endpoint_time = time.time()


async def _heartbeat_loop():
    """Write a heartbeat line every HEARTBEAT_INTERVAL seconds."""
    log = _get_logger()
    log.info(f"[HB] Heartbeat started — interval={HEARTBEAT_INTERVAL}s, pid={os.getpid()}")
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            mem = _memory_info()
            tasks = _active_tasks_summary()
            log.info(
                f"[HB] {_now_iso()} | "
                f"RSS={mem.get('rss_mb', '?')}MB | "
                f"Avail={mem.get('system_available_gb', '?')}GB | "
                f"SysMem={mem.get('system_percent_used', '?')}% | "
                f"Tasks={len(tasks)} | "
                f"Last={_last_endpoint or 'none'}"
            )
        except asyncio.CancelledError:
            log.info(f"[HB] Heartbeat stopped at {_now_iso()}")
            break
        except Exception as e:
            # Don't let heartbeat crash take down the app
            try:
                log.warning(f"[HB] Heartbeat error: {e}")
            except Exception:
                pass


def start_heartbeat():
    """Start the heartbeat background task. Call after event loop is running."""
    global _heartbeat_task
    from utils.tasks import safe_create_task
    _heartbeat_task = safe_create_task(_heartbeat_loop(), name="diagnostics-heartbeat")
    return _heartbeat_task


def stop_heartbeat():
    """Cancel the heartbeat task."""
    global _heartbeat_task
    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()


# ─── Log Reader (for health portal endpoint) ─────────────────────────────────

def read_diagnostics_log(tail_lines: int = 100) -> str:
    """Read the last N lines of the diagnostics log."""
    log_path = _get_log_path()
    if not log_path.exists():
        return "(no diagnostics log yet)"
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-tail_lines:])
    except Exception as e:
        return f"(error reading log: {e})"


def read_crash_snapshots() -> list:
    """Extract crash snapshots from the diagnostics log."""
    log_path = _get_log_path()
    snapshots = []
    if not log_path.exists():
        return snapshots
    try:
        text = log_path.read_text(encoding="utf-8")
        # Also check backup files
        for i in range(1, BACKUP_COUNT + 1):
            backup = Path(f"{log_path}.{i}")
            if backup.exists():
                text = backup.read_text(encoding="utf-8") + "\n" + text

        blocks = text.split("=" * 60)
        for i, block in enumerate(blocks):
            if "CRASH SNAPSHOT" in block:
                snapshots.append(block.strip())
    except Exception:
        pass
    return snapshots
