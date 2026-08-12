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

P1 (2026-08-12) adds the data + emit contract:

  • **Read-only SQLite** via `open_db` / `query` / `read_df` over the paths in `data_files`. Uses the
    stdlib `sqlite3` URI mode (`?mode=ro`) plus pandas — both already bundled, so this needs **no new
    dependency**. (The original plan called for DuckDB; stdlib+pandas covers every SQLite-backed
    candidate in `READFIRST/planning/python-tier-dashboard-workflows.md` at zero bundle cost. Revisit
    DuckDB only if we need to query the JSON stores directly or join across formats.)
  • **Emit helpers** — `emit_chart` / `emit_table` / `emit_markdown` / `emit_html` / `emit_svg`.
    `emit_chart` → `json:chart` is the PRIMARY verb: the sandboxed `interactive-html` renderer blocks
    all external URLs, so charts must travel as *data* rendered outside the sandbox (recharts), never
    as CDN markup inside it. Several charts beat one monolithic HTML blob.
  • **Multiple artifacts per run** (`result["artifacts"]`), because the flagship dashboards are
    multi-chart. `result["artifact"]` stays as the first one for single-artifact callers.

Callers land in P2+ (Studio doc charts first).
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


def _build_data_helpers(data_files: Dict[str, str]) -> Dict[str, Any]:
    """Read-only data access over the caller's `data_files` (name → path).

    Read-only is enforced at the connection (`file:…?mode=ro`), so the sandbox physically cannot
    write to a real store — which matters because on a dev venv `settings.data_dir` IS the production
    data directory. `immutable=1` is deliberately NOT used: it promises the file never changes, and a
    live app writing to the same SQLite would then hand us stale pages."""
    import sqlite3

    def _path(name: str) -> str:
        if name not in data_files:
            raise KeyError(f"unknown data file {name!r}; available: {sorted(data_files)}")
        return data_files[name]

    def open_db(name: str):
        """Read-only sqlite3 connection, rows accessible by column name."""
        conn = sqlite3.connect(f"file:{_path(name)}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def query(name: str, sql: str, params: Any = ()) -> list:
        """Run SQL against `name`, return a list of plain dicts."""
        conn = open_db(name)
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def read_df(name: str, sql: str, params: Any = ()):
        """Run SQL against `name`, return a pandas DataFrame."""
        try:
            import pandas as pd
        except ImportError as e:  # pragma: no cover — pandas is a bundled dep
            raise RuntimeError("pandas is unavailable in the sandbox") from e
        conn = open_db(name)
        try:
            return pd.read_sql_query(sql, conn, params=params)
        finally:
            conn.close()

    def tables(name: str) -> list:
        """List the table/view names in `name` — lets generated code discover shape before querying."""
        return [r["name"] for r in query(
            name, "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]

    return {"open_db": open_db, "query": query, "read_df": read_df, "tables": tables}


def _build_emit_helpers(sink: list) -> Dict[str, Any]:
    """Typed `emit_*` helpers. Each appends one Artifact-shaped dict to `sink`.

    These build plain dicts rather than importing `artifact_spec` so the sandbox stays independent of
    the parent's model layer; the parent validates shapes after the run."""

    def _artifact(kind: str, payload: Any, title, id_, **extra) -> dict:
        art = {"id": id_ or f"pyc-{len(sink) + 1}", "type": kind, "payload": payload,
               "metadata": {"source": "py_compute", **extra.pop("metadata", {})}}
        if title:
            art["title"] = title
        art.update(extra)
        return art

    def emit(artifact: Any) -> None:
        """Escape hatch — emit a raw Artifact dict (or anything with .model_dump())."""
        if hasattr(artifact, "model_dump"):
            artifact = artifact.model_dump()
        if not isinstance(artifact, dict):
            raise TypeError("emit() expects an Artifact dict (or a pydantic Artifact)")
        sink.append(artifact)

    def emit_chart(chart_type: str, data: Any, series: Any, *, title=None, x_key=None,
                   x_label=None, y_label=None, stacked=False, id=None) -> None:
        """THE primary verb. `series` accepts ['col'] / [{'key','label',...}]; `data` accepts a list
        of dicts or a pandas DataFrame. Renders via recharts OUTSIDE the sandbox."""
        if hasattr(data, "to_dict"):           # pandas DataFrame
            data = data.to_dict(orient="records")
        norm = [{"key": s} if isinstance(s, str) else dict(s) for s in (series or [])]
        cfg: Dict[str, Any] = {"chart_type": chart_type, "series": norm,
                               "data": list(data or []), "stacked": bool(stacked)}
        if title:
            cfg["title"] = title
        if x_key or x_label:
            cfg["x_axis"] = {k: v for k, v in (("key", x_key), ("label", x_label)) if v}
        if y_label:
            cfg["y_axis"] = {"label": y_label}
        sink.append(_artifact("json:chart", cfg, title, id))

    def emit_table(rows: Any, *, title=None, columns=None, id=None) -> None:
        """Emit rows as a markdown table (renders through the existing markdown renderer)."""
        if hasattr(rows, "to_dict"):
            rows = rows.to_dict(orient="records")
        rows = list(rows or [])
        cols = list(columns or (rows[0].keys() if rows else []))
        head = "| " + " | ".join(str(c) for c in cols) + " |"
        rule = "| " + " | ".join("---" for _ in cols) + " |"
        body = ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
        sink.append(_artifact("markdown", "\n".join([head, rule, *body]), title, id))

    def emit_markdown(text: str, *, title=None, id=None) -> None:
        sink.append(_artifact("markdown", str(text), title, id))

    def emit_svg(svg: str, *, title=None, id=None) -> None:
        sink.append(_artifact("svg", str(svg), title, id))

    def emit_html(html: str, *, title=None, interactive=False, id=None) -> None:
        """`interactive=True` targets the SANDBOXED iframe renderer, which blocks every external URL
        — the HTML must be fully self-contained (no CDN scripts, fonts or images). Prefer several
        emit_chart() calls over one big HTML dashboard."""
        sink.append(_artifact("interactive-html" if interactive else "html", str(html), title, id))

    return {"emit": emit, "emit_chart": emit_chart, "emit_table": emit_table,
            "emit_markdown": emit_markdown, "emit_svg": emit_svg, "emit_html": emit_html}


def _sandbox_child(code: str, data_files: Dict[str, str], limits_dict: dict, conn) -> None:
    """Runs in the spawned child: apply limits, exec the user code with an `emit()` helper, capture
    stdout, and send back `{ok, stdout, artifact, error}`. Never lets anything escape unreported."""
    result = {"ok": False, "stdout": "", "artifact": None, "artifacts": [], "error": None}
    try:
        limits = SandboxLimits(**limits_dict)
        _apply_rlimits(limits)
        _block_network()

        sink: list = []
        ns: Dict[str, Any] = {
            "__name__": "__sandbox__",
            "DATA_FILES": dict(data_files or {}),
            **_build_emit_helpers(sink),
            **_build_data_helpers(dict(data_files or {})),
        }

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<py_compute>", "exec"), ns)
            result["ok"] = True
            if sink:
                blob = json.dumps(sink, default=str)  # default=str tolerates numpy/Decimal/datetime
                if len(blob) > _MAX_ARTIFACT_BYTES:
                    result["ok"] = False
                    result["error"] = (f"emitted artifacts too large ({len(blob)} bytes across "
                                       f"{len(sink)} artifact(s))")
                else:
                    arts = json.loads(blob)  # ensure they are JSON-clean
                    result["artifacts"] = arts
                    result["artifact"] = arts[0]
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


def _fail(error: str, stdout: str = "") -> Dict[str, Any]:
    return {"ok": False, "stdout": stdout, "artifact": None, "artifacts": [], "error": error}


def _validate_charts(result: Dict[str, Any]) -> Dict[str, Any]:
    """Validate every emitted `json:chart` payload against `ChartConfig` in the PARENT.

    Done here, not in the child, so the sandbox stays free of the model layer and a schema drift
    surfaces as a clean error instead of an unrenderable chart reaching the frontend. Mirrors what
    `visual_resolver._resolve_chart` does for LLM-authored fences."""
    charts = [a for a in result.get("artifacts") or [] if a.get("type") == "json:chart"]
    if not charts:
        return result
    try:
        from services.chart_spec import ChartConfig
    except Exception:  # pydantic/schema unavailable — don't fail the run over validation
        return result
    for art in charts:
        try:
            ChartConfig(**(art.get("payload") or {}))
        except Exception as e:
            result["ok"] = False
            result["error"] = f"emitted chart {art.get('id')!r} failed ChartConfig validation: {e}"
            break
    return result


def run(
    code: str,
    *,
    data_files: Optional[Dict[str, str]] = None,
    limits: Optional[SandboxLimits] = None,
) -> Dict[str, Any]:
    """Execute `code` in the sandbox → `{ok, stdout, artifact, artifacts, error}`. Never raises.

    The code may `print(...)` (captured) and call the emit helpers any number of times;
    `artifacts` is everything emitted, `artifact` the first. `data_files` is a name→path map,
    exposed as the `DATA_FILES` dict and queryable read-only via `query` / `read_df` / `tables`."""
    limits = limits or SandboxLimits()
    if not (code or "").strip():
        return _fail("empty code")

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
                result = _fail("sandbox died before returning a result")
        else:
            result = _fail(f"timeout after {limits.timeout_s}s")
    except Exception as e:
        result = _fail(f"sandbox launch failed: {type(e).__name__}: {e}")
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        _terminate(proc)
    return _validate_charts(result)


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
