"""Research jobs a companion can start and poll — persisted, so they survive a restart.

The Jocasta contract: `start_research(topic, notebook_id?)` returns a job id at
once; `get_job(job_id)` reports it. "Jobs must survive a LocalBook restart" —
the in-memory `job_queue` cannot promise that, so jobs live in `research_jobs`
(localbook.db, per-machine: a job belongs to the Mac that ran it). A job that
was queued or running when the app stopped is re-run at startup rather than
left "running" forever.

One job at a time: a deep dive is minutes of scraping plus LLM scoring, and a
companion must not stack them up against the user's own work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_RESULTS = 10
_lock: Optional[asyncio.Lock] = None


def _conn():
    from storage.database import get_db

    conn = get_db().get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS research_jobs (
        id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        notebook_id TEXT,
        companion_id TEXT,
        status TEXT NOT NULL,          -- queued | running | done | error
        results_json TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    return conn


def _set(job_id: str, **fields) -> None:
    fields["updated_at"] = datetime.utcnow().isoformat()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _conn()
    conn.execute(f"UPDATE research_jobs SET {cols} WHERE id = ?", [*fields.values(), job_id])
    conn.commit()


def get(job_id: str) -> Optional[Dict[str, Any]]:
    row = _conn().execute("SELECT * FROM research_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["results"] = json.loads(job.pop("results_json") or "[]")
    return job


def create(topic: str, notebook_id: Optional[str], companion_id: str) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()
    job_id = uuid.uuid4().hex
    conn = _conn()
    conn.execute(
        "INSERT INTO research_jobs (id, topic, notebook_id, companion_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'queued', ?, ?)", (job_id, topic, notebook_id, companion_id, now, now))
    conn.commit()
    return get(job_id)          # the caller launches it, on the event loop


def _result(r) -> Dict[str, Any]:
    d = asdict(r)
    d.pop("full_text", None)          # a job result is a reading list, not the pages
    return d


async def _run(job_id: str) -> None:
    global _lock
    _lock = _lock or asyncio.Lock()
    async with _lock:
        job = get(job_id)
        if not job or job["status"] in ("done", "error"):
            return
        _set(job_id, status="running", attempts=int(job.get("attempts") or 0) + 1)
        try:
            from services.research_engine import research_engine

            results = await research_engine.deep_dive(job["topic"], job.get("notebook_id") or "")
            _set(job_id, status="done",
                 results_json=json.dumps([_result(r) for r in results[:MAX_RESULTS]]))
        except Exception as exc:
            logger.warning("[research-jobs] %s failed: %s", job_id, exc)
            _set(job_id, status="error", error=str(exc)[:500])


def launch(job_id: str) -> None:
    """Start a job. Call from the event loop — `create` and `resume_interrupted`
    are sync (they run in a worker thread), so they never launch themselves."""
    from utils.tasks import safe_create_task

    safe_create_task(_run(job_id), name=f"research-job-{job_id[:8]}")


def resume_interrupted() -> List[str]:
    """At startup: which jobs a restart cut off (re-queued; the caller launches
    them). A job interrupted twice is marked failed rather than retried forever."""
    rows = _conn().execute(
        "SELECT id, attempts FROM research_jobs WHERE status IN ('queued', 'running')").fetchall()
    resumed = []
    for job_id, attempts in rows:
        if int(attempts or 0) >= 2:
            _set(job_id, status="error", error="interrupted twice by a restart")
            continue
        _set(job_id, status="queued")
        resumed.append(job_id)
    return resumed
