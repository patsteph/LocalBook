"""
Sidecar Manager — Phase 2 lifecycle management for llama-server.

Responsibilities:
- Spawn `llama-server` as a child process with the correct flags for a
  registered llama_server-provider model (Bonsai-8B today).
- Poll the sidecar's /health endpoint until ready, with a caller-supplied
  timeout, so the rest of the app can rely on "sidecar is up" after start().
- Terminate the child gracefully on shutdown (SIGTERM, then SIGKILL fallback).
- Expose a `SidecarManager` singleton for import from lifespan + API routes.

Design invariants:
- Never blocks the event loop. All subprocess + sleep work uses asyncio.
- Never raises from background start attempts — failures are logged and the
  caller can inspect `.last_error`.
- Safe to call start() multiple times: a second call while a child is alive
  and healthy returns immediately; a call during a failed-to-start state
  is idempotent (cleans up the stale pid before retrying).
- Config is layered: env vars (highest) → user_preferences.json → built-in
  defaults. This keeps the dev launcher (`scripts/start_bonsai_sidecar.sh`)
  behaviour identical for manual mode.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from config import settings as app_settings

logger = logging.getLogger(__name__)


# ─── Config resolution ──────────────────────────────────────────────────────────

DEFAULT_MODEL_PATH = Path.home() / ".localbook" / "models" / "bonsai" / "Bonsai-8B-Q1_0.gguf"
DEFAULT_PORT = 8090
DEFAULT_CTX_SIZE = 65536   # Bonsai-8B native context (64k); override via BONSAI_CTX_SIZE if memory-tight
DEFAULT_NGL = 99

# Candidate binary locations, checked in order.
_BINARY_CANDIDATES = [
    str(Path.home() / "src" / "llama.cpp" / "build" / "bin" / "llama-server"),
    "/opt/homebrew/bin/llama-server",
    "/usr/local/bin/llama-server",
]


@dataclass
class SidecarConfig:
    """Resolved, ready-to-use sidecar configuration."""
    binary_path: str
    model_path: str
    port: int
    ctx_size: int
    ngl: int
    threads: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _prefs_sidecar_overrides() -> dict:
    """Read sidecar-specific overrides from user_preferences.json.

    Shape:
        {"sidecar": {"binary_path": "...", "model_path": "...",
                     "port": 8090, "ctx_size": 4096, "ngl": 99,
                     "auto_start": false}}
    Any keys missing fall back to env vars / built-in defaults.
    """
    try:
        path = app_settings.data_dir / "user_preferences.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        sc = data.get("sidecar", {})
        return sc if isinstance(sc, dict) else {}
    except Exception as _e:
        logger.debug(f"[sidecar] prefs read failed: {_e}")
        return {}


def _resolve_binary(override: Optional[str]) -> Optional[str]:
    """Return an executable `llama-server` path, or None if none found."""
    if override and os.access(override, os.X_OK):
        return override
    for cand in _BINARY_CANDIDATES:
        if cand and os.access(cand, os.X_OK):
            return cand
    which_result = shutil.which("llama-server")
    return which_result


def _physical_threads() -> int:
    """Performance-core count on Apple Silicon, fallback to hw.ncpu elsewhere."""
    for key in ("hw.perflevel0.physicalcpu", "hw.ncpu"):
        try:
            out = subprocess.check_output(["sysctl", "-n", key], text=True).strip()
            n = int(out)
            if n > 0:
                return n
        except Exception:
            continue
    return 4


def resolve_config() -> SidecarConfig:
    """Layer env vars over user_preferences over built-in defaults."""
    prefs = _prefs_sidecar_overrides()

    binary = _resolve_binary(
        os.environ.get("BONSAI_BIN") or prefs.get("binary_path")
    )
    model_path = (
        os.environ.get("BONSAI_MODEL_PATH")
        or prefs.get("model_path")
        or str(DEFAULT_MODEL_PATH)
    )
    port = int(os.environ.get("BONSAI_PORT") or prefs.get("port") or DEFAULT_PORT)
    ctx_size = int(os.environ.get("BONSAI_CTX_SIZE") or prefs.get("ctx_size") or DEFAULT_CTX_SIZE)
    ngl = int(os.environ.get("BONSAI_NGL") or prefs.get("ngl") or DEFAULT_NGL)
    threads = _physical_threads()

    return SidecarConfig(
        binary_path=binary or "",
        model_path=model_path,
        port=port,
        ctx_size=ctx_size,
        ngl=ngl,
        threads=threads,
    )


# ─── Manager ───────────────────────────────────────────────────────────────────

class SidecarManager:
    """Lifecycle controller for the llama-server child process.

    Thread-safety: all public methods are async and rely on asyncio.Lock to
    serialize start/stop. Status queries are lock-free snapshots.
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._started_at: float = 0.0
        self._last_error: str = ""
        self._lock = asyncio.Lock()
        self._log_path = Path("/tmp/bonsai-server.log")
        self._err_path = Path("/tmp/bonsai-server.err")

    # ── Status ────────────────────────────────────────────────────────────

    @property
    def last_error(self) -> str:
        return self._last_error

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def is_healthy(self, timeout: float = 1.0) -> bool:
        cfg = resolve_config()
        url = f"{cfg.base_url}/health"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url)
                return r.status_code == 200
        except Exception:
            return False

    async def status(self) -> dict:
        """Snapshot for the /providers/sidecar/status endpoint.

        We consider the sidecar "running" if either (a) we own a live child
        process, or (b) /health responds on the configured port (foreign
        process started by the dev script). This makes the UI honest when
        users straddle manual and auto-start flows.
        """
        cfg = resolve_config()
        owned_alive = self.is_running()
        healthy = await self.is_healthy()
        # Foreign sidecars have no PID we can surface and no reliable uptime.
        pid = self._proc.pid if owned_alive else None
        uptime = (time.time() - self._started_at) if (owned_alive and self._started_at) else 0
        return {
            "running": owned_alive or healthy,
            "owned": owned_alive,
            "healthy": healthy,
            "pid": pid,
            "uptime_seconds": uptime,
            "binary_path": cfg.binary_path,
            "model_path": cfg.model_path,
            "model_exists": os.path.exists(cfg.model_path),
            "port": cfg.port,
            "last_error": self._last_error,
        }

    # ── Start / stop ──────────────────────────────────────────────────────

    async def start(self, timeout: float = 45.0) -> bool:
        """Spawn the sidecar if not already running. Wait until /health is 200.

        Returns True iff the sidecar is healthy when this coroutine returns.
        Multi-call safe: if a healthy process is already running, returns True
        immediately without touching it.
        """
        async with self._lock:
            # Fast path: already running and healthy
            if self.is_running() and await self.is_healthy():
                return True

            # Stale process — clean it up before retry
            if self._proc is not None and self._proc.poll() is not None:
                logger.warning(f"[sidecar] pruning dead child pid={self._proc.pid}")
                self._proc = None
                self._started_at = 0.0

            cfg = resolve_config()
            self._last_error = ""

            # Pre-flight validation — fail fast with clear messages
            if not cfg.binary_path:
                self._last_error = (
                    "llama-server binary not found. Install llama.cpp via Homebrew "
                    "or build from source to ~/src/llama.cpp/build/bin/llama-server."
                )
                logger.error(f"[sidecar] {self._last_error}")
                return False
            if not os.path.exists(cfg.model_path):
                self._last_error = f"Model file not found: {cfg.model_path}"
                logger.error(f"[sidecar] {self._last_error}")
                return False
            if _port_in_use(cfg.port):
                # Someone else is on this port — treat as healthy if it *is*
                # in fact a running sidecar, else error.
                if await self.is_healthy():
                    logger.info(f"[sidecar] port {cfg.port} already answers /health — adopting")
                    self._started_at = self._started_at or time.time()
                    return True
                self._last_error = f"Port {cfg.port} is in use by a non-sidecar process."
                logger.error(f"[sidecar] {self._last_error}")
                return False

            cmd = [
                cfg.binary_path,
                "-m", cfg.model_path,
                "--host", "127.0.0.1",
                "--port", str(cfg.port),
                "-ngl", str(cfg.ngl),
                "--ctx-size", str(cfg.ctx_size),
                "--threads", str(cfg.threads),
            ]
            logger.info(f"[sidecar] spawning: {' '.join(cmd)}")

            try:
                # Open log files append-mode; rotated manually or tail -f by user
                log_f = open(self._log_path, "ab")
                err_f = open(self._err_path, "ab")
                # detach from our process group so a SIGINT on the backend
                # doesn't immediately propagate to the child (we still stop
                # it explicitly on shutdown)
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=err_f,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._started_at = time.time()
            except Exception as e:
                self._last_error = f"Failed to spawn llama-server: {e}"
                logger.exception(f"[sidecar] spawn failed: {e}")
                return False

            # Poll /health until ready or timeout
            deadline = time.monotonic() + timeout
            poll_interval = 0.5
            while time.monotonic() < deadline:
                # Child already exited?
                rc = self._proc.poll()
                if rc is not None:
                    self._last_error = (
                        f"llama-server exited during startup (rc={rc}). "
                        f"See {self._err_path} for details."
                    )
                    logger.error(f"[sidecar] {self._last_error}")
                    self._proc = None
                    return False
                if await self.is_healthy(timeout=1.0):
                    elapsed = time.time() - self._started_at
                    logger.info(
                        f"[sidecar] healthy after {elapsed:.1f}s "
                        f"(pid={self._proc.pid}, port={cfg.port})"
                    )
                    return True
                await asyncio.sleep(poll_interval)

            # Timeout: kill what we started to avoid leaks
            self._last_error = f"Sidecar did not become healthy within {timeout}s"
            logger.error(f"[sidecar] {self._last_error}")
            await self._terminate_child(grace_seconds=3.0)
            return False

    async def stop(self, grace_seconds: float = 5.0) -> None:
        async with self._lock:
            await self._terminate_child(grace_seconds=grace_seconds)

    async def _terminate_child(self, *, grace_seconds: float) -> None:
        """Send SIGTERM, wait, fall back to SIGKILL. Always clears self._proc."""
        if self._proc is None:
            return
        if self._proc.poll() is not None:
            self._proc = None
            self._started_at = 0.0
            return

        pid = self._proc.pid
        try:
            logger.info(f"[sidecar] SIGTERM pid={pid}")
            self._proc.terminate()
        except Exception as e:
            logger.warning(f"[sidecar] SIGTERM failed: {e}")

        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                break
            await asyncio.sleep(0.2)

        if self._proc.poll() is None:
            logger.warning(f"[sidecar] SIGKILL pid={pid}")
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            # Brief wait to reap
            try:
                self._proc.wait(timeout=2.0)
            except Exception:
                pass

        self._proc = None
        self._started_at = 0.0

    async def ensure_started(self, timeout: float = 45.0) -> bool:
        """Start if not healthy; no-op if already healthy."""
        if self.is_running() and await self.is_healthy():
            return True
        return await self.start(timeout=timeout)


def _port_in_use(port: int) -> bool:
    """Quick TCP probe without raising."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except Exception:
            return False


# ─── Singleton + auto-start helper ─────────────────────────────────────────────

sidecar_manager = SidecarManager()


def is_active_model_sidecar_backed() -> bool:
    """Return True iff the current main/fast model is a llama_server provider.

    Used by the FastAPI lifespan to decide whether to auto-start the sidecar.
    Defensive: if the registry/provider resolver throws, we default False so
    a broken registry never blocks backend boot.
    """
    try:
        from services.llm_provider import resolve as _resolve_provider, Provider
        for mname in (app_settings.ollama_model, app_settings.ollama_fast_model):
            if not mname:
                continue
            if _resolve_provider(mname).provider is Provider.LLAMA_SERVER:
                return True
    except Exception as _e:
        logger.debug(f"[sidecar] active-model check failed: {_e}")
    return False


def auto_start_enabled_in_prefs() -> bool:
    """True iff user_preferences.json → sidecar.auto_start is truthy."""
    try:
        return bool(_prefs_sidecar_overrides().get("auto_start", False))
    except Exception:
        return False


async def maybe_auto_start_on_boot() -> None:
    """Called from the FastAPI lifespan. Lazy policy: only start if needed.

    Triggers start when:
      1. The active main_model or fast_model is a llama_server-provider model; OR
      2. The user set sidecar.auto_start=true in user_preferences.json.
    Never blocks or crashes boot.
    """
    try:
        if is_active_model_sidecar_backed() or auto_start_enabled_in_prefs():
            logger.info("[sidecar] auto-start triggered by active model / prefs")
            ok = await sidecar_manager.ensure_started(timeout=45.0)
            if not ok:
                logger.warning(
                    f"[sidecar] auto-start failed: {sidecar_manager.last_error}"
                )
    except Exception as e:
        logger.warning(f"[sidecar] auto_start swallowed error: {e}")
