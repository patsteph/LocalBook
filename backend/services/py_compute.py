"""py_compute — a sandboxed "generate Python → run → emit an Artifact" execution tier.

General app capability (P0 of the Python hard-compute tier). Runs short, app- or model-generated
Python in an isolated child process with resource limits, no network, and captured stdout, and lets
it `emit()` a renderable Artifact (chart / html / markdown / table) that plugs straight into the
existing renderer registry. This supersedes the dead in-process `rlm_executor` `exec()` prototype
(unused, bypassable substring-denylist "sandbox").

Isolation is proportionate to LocalBook's single-user, fully-offline threat model — the goal is
preventing runaway loops, memory blowups, and accidental damage, NOT defending against an attacker
who already has the user's shell:

  • Separate process via multiprocessing **spawn** — works inside the frozen PyInstaller app
    (main.py already calls `multiprocessing.freeze_support()`); a `fork` would risk deadlock in this
    multithreaded asyncio process (inherited locks).
  • POSIX **rlimits** applied in the child: CPU seconds (SIGXCPU), address space, max file size,
    open files. Each is best-effort (e.g. RLIMIT_AS is weakly enforced on macOS) with the wall-clock
    timeout as the hard backstop.
  • Wall-clock **timeout** in the parent → `terminate()` then `kill()`.
  • **Network** neutralized at the Python level (`socket.socket` / `create_connection` raise) — the
    common exfil path. Packet-level isolation on macOS needs sandbox-exec/namespaces (future
    hardening; noted, not required for the offline model).

Data access (read-only DuckDB over the notebook SQLite `.db`) + richer emit helpers land in P1;
callers land in P2+ (Studio doc charts first).
"""
from __future__ import annotations

import io
import json
import logging
import contextlib
import multiprocessing as mp
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_STDOUT = 100_000          # cap captured stdout (chars)
_MAX_ARTIFACT_BYTES = 2_000_000  # cap the emitted artifact (serialized bytes)


@dataclass
class SandboxLimits:
    """Resource caps for one sandboxed run. `cpu_s` is kept below `timeout_s` so a CPU spin dies on
    SIGXCPU first, with the wall-clock timeout as the hard backstop."""
    timeout_s: float = 10.0
    cpu_s: int = 8
    mem_mb: int = 512
    fsize_mb: int = 64
    nofile: int = 64


# ── child-side helpers (run in the spawned process) ──────────────────────────────────
def _apply_rlimits(limits: "SandboxLimits") -> None:
    """Best-effort POSIX resource caps. Each guarded so an unsupported/hardened limit on a given OS
    never aborts the run."""
    try:
        import resource
    except Exception:
        return

    def _set(res_id, soft):
        try:
            _, hard = resource.getrlimit(res_id)
            cap = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
            resource.setrlimit(res_id, (cap, hard))
        except Exception:
            pass

    _set(resource.RLIMIT_CPU, int(limits.cpu_s))
    if limits.mem_mb:
        _set(getattr(resource, "RLIMIT_AS", resource.RLIMIT_DATA), int(limits.mem_mb) * 1024 * 1024)
    _set(resource.RLIMIT_FSIZE, int(limits.fsize_mb) * 1024 * 1024)
    _set(resource.RLIMIT_NOFILE, int(limits.nofile))


def _block_network() -> None:
    """Neutralize outbound network access. Blocks the *actions* that reach the network (connect,
    create_connection, DNS) rather than the `socket.socket` *type* — the stdlib `ssl` module does
    `class SSLSocket(socket)`, so replacing the class itself breaks any transitive `import ssl`
    (e.g. via pandas/duckdb/requests). A socket can be constructed but never connect."""
    try:
        import socket

        def _blocked(*_a, **_k):
            raise RuntimeError("network access is disabled in the py_compute sandbox")

        socket.socket.connect = _blocked      # type: ignore[assignment]
        socket.socket.connect_ex = _blocked   # type: ignore[assignment]
        socket.create_connection = _blocked   # type: ignore[assignment]
        socket.getaddrinfo = _blocked         # type: ignore[assignment]  # kill DNS too
    except Exception:
        pass


def _sandbox_child(code: str, data_files: Dict[str, str], limits_dict: dict, conn) -> None:
    """Runs in the spawned child: apply limits, exec the user code with an `emit()` helper, capture
    stdout, and send back `{ok, stdout, artifact, error}`. Never lets anything escape unreported."""
    result = {"ok": False, "stdout": "", "artifact": None, "error": None}
    try:
        limits = SandboxLimits(**limits_dict)
        _apply_rlimits(limits)
        _block_network()

        emitted: Dict[str, Any] = {"artifact": None}

        def emit(artifact: Any) -> None:
            if hasattr(artifact, "model_dump"):
                artifact = artifact.model_dump()  # accept a pydantic Artifact directly
            if not isinstance(artifact, dict):
                raise TypeError("emit() expects an Artifact dict (or a pydantic Artifact)")
            emitted["artifact"] = artifact

        ns: Dict[str, Any] = {
            "__name__": "__sandbox__",
            "emit": emit,
            "DATA_FILES": dict(data_files or {}),
        }

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<py_compute>", "exec"), ns)
            result["ok"] = True
            art = emitted["artifact"]
            if art is not None:
                blob = json.dumps(art, default=str)
                if len(blob) > _MAX_ARTIFACT_BYTES:
                    result["ok"] = False
                    result["error"] = f"emitted artifact too large ({len(blob)} bytes)"
                else:
                    result["artifact"] = json.loads(blob)  # ensure it is JSON-clean
        except BaseException as e:  # user exception, SIGXCPU→SystemExit, etc.
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            result["stdout"] = buf.getvalue()[:_MAX_STDOUT]
    except BaseException as e:  # sandbox setup itself failed
        result["error"] = f"sandbox setup failed: {type(e).__name__}: {e}"
    finally:
        try:
            conn.send(result)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ── parent-side API ──────────────────────────────────────────────────────────────────
def _terminate(proc) -> None:
    try:
        if proc.is_alive():
            proc.terminate()
            proc.join(1.0)
        if proc.is_alive():
            proc.kill()
            proc.join(1.0)
    except Exception:
        pass


def run(
    code: str,
    *,
    data_files: Optional[Dict[str, str]] = None,
    limits: Optional[SandboxLimits] = None,
) -> Dict[str, Any]:
    """Execute `code` in the sandbox and return `{ok, stdout, artifact, error}`. Never raises.

    The code may `print(...)` (captured) and call `emit(artifact_dict)` once to return a renderable
    Artifact. `data_files` is a name→path map exposed to the code as the `DATA_FILES` dict (read-only
    convention; the DuckDB helper lands in P1)."""
    limits = limits or SandboxLimits()
    if not (code or "").strip():
        return {"ok": False, "stdout": "", "artifact": None, "error": "empty code"}

    ctx = mp.get_context("spawn")  # thread-safe, no global set_start_method; frozen-app safe
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_sandbox_child,
        args=(code, dict(data_files or {}), asdict(limits), child_conn),
        daemon=True,
    )
    try:
        proc.start()
        child_conn.close()  # the parent keeps only the read end
        if parent_conn.poll(limits.timeout_s):
            try:
                result = parent_conn.recv()
            except EOFError:
                result = {"ok": False, "stdout": "", "artifact": None,
                          "error": "sandbox died before returning a result"}
        else:
            result = {"ok": False, "stdout": "", "artifact": None,
                      "error": f"timeout after {limits.timeout_s}s"}
    except Exception as e:
        result = {"ok": False, "stdout": "", "artifact": None,
                  "error": f"sandbox launch failed: {type(e).__name__}: {e}"}
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        _terminate(proc)
    return result


async def run_async(
    code: str,
    *,
    data_files: Optional[Dict[str, str]] = None,
    limits: Optional[SandboxLimits] = None,
) -> Dict[str, Any]:
    """Async wrapper — offloads the blocking sandbox run to a thread so callers on the event loop
    (chat/content pipelines) never block. Never raises."""
    import asyncio

    return await asyncio.to_thread(run, code, data_files=data_files, limits=limits)
