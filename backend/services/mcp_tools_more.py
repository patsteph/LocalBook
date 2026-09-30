"""The rest of the Jocasta contract's MCP tools (2026-09-30).

`get_note`, `list_recent_notes`, `youtube_search`, `scholarly_search`,
`start_research` + `get_job`. Beside `mcp_server` (which is past 700 lines) and
registered onto the same server by `register()`, with the same audit wrapper,
bounds and busy/scope conventions. Tool and argument names are the contract:
Hermes skills call them by name.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Dict, Optional

NOTE_CHARS = 20_000
SCHOLARLY_SITES = {
    "semanticscholar": "semanticscholar.org",
    "arxiv": "arxiv.org",
    "openalex": "openalex.org",
    "pubmed": "pubmed.ncbi.nlm.nih.gov",
}


def register(mcp, *, read_only, starts_work, audited, bounded_k, current_caller) -> None:

    @mcp.tool(annotations=read_only)
    async def get_note(note_id: str, max_chars: int = NOTE_CHARS) -> Dict[str, Any]:
        """Read one of the user's notes (canvas notes), by id."""
        max_chars = max(200, min(int(max_chars or NOTE_CHARS), NOTE_CHARS))
        async with audited("get_note", {"note_id": note_id}):
            from storage.note_store import note_store

            note = await note_store.get(note_id)
            if not note:
                return {"error": f"no note with id {note_id}"}
            body = note.get("content_markdown") or ""
            return {
                "id": note["id"], "title": note.get("title"),
                "notebook_id": note.get("notebook_id"), "tags": note.get("tags") or [],
                "created_at": note.get("created_at"), "updated_at": note.get("updated_at"),
                "text": body[:max_chars], "total_chars": len(body),
                "truncated": len(body) > max_chars,
            }

    @mcp.tool(annotations=read_only)
    async def list_recent_notes(n: int = 10) -> Dict[str, Any]:
        """The user's most recently edited notes, newest first (titles + a preview)."""
        n = bounded_k(n, 10)
        async with audited("list_recent_notes", {"n": n}):
            from storage.note_store import note_store

            notes = (await note_store.list_all())[:n]
            return {"notes": [{
                "id": x["id"], "title": x.get("title"), "notebook_id": x.get("notebook_id"),
                "updated_at": x.get("updated_at"),
                "preview": (x.get("content_markdown") or "")[:200],
            } for x in notes]}

    async def _site(query: str, domain: str, n: int):
        from services.site_search import site_search_service

        return [asdict(r) for r in await site_search_service.search(query, site_domain=domain,
                                                                     max_results=n)]

    @mcp.tool(annotations=read_only)
    async def youtube_search(query: str, n: int = 5) -> Dict[str, Any]:
        """Search YouTube for videos (needs a YouTube key in Settings; falls back to web search)."""
        n = bounded_k(n, 5)
        async with audited("youtube_search", {"query": query, "n": n}):
            return {"results": await _site(query, "youtube.com", n)}

    @mcp.tool(annotations=read_only)
    async def scholarly_search(query: str, n: int = 5, source: str = "all") -> Dict[str, Any]:
        """Search academic papers. `source`: "all" (Semantic Scholar + arXiv),
        "semanticscholar", "arxiv", "openalex" or "pubmed"."""
        n = bounded_k(n, 5)
        if source != "all" and source not in SCHOLARLY_SITES:
            return {"error": f"source must be 'all' or one of {sorted(SCHOLARLY_SITES)}"}
        async with audited("scholarly_search", {"query": query, "n": n, "source": source}):
            if source != "all":
                return {"results": await _site(query, SCHOLARLY_SITES[source], n)}
            parts = await asyncio.gather(
                _site(query, SCHOLARLY_SITES["semanticscholar"], n),
                _site(query, SCHOLARLY_SITES["arxiv"], n),
                return_exceptions=True)
            merged, seen = [], set()
            # Interleave so neither index crowds the other out of the top n.
            for pair in zip(*[p if isinstance(p, list) else [] for p in parts]):
                for r in pair:
                    if r["url"] not in seen:
                        seen.add(r["url"])
                        merged.append(r)
            for p in parts:
                for r in (p if isinstance(p, list) else []):
                    if r["url"] not in seen:
                        seen.add(r["url"])
                        merged.append(r)
            return {"results": merged[:n]}

    @mcp.tool(annotations=starts_work)
    async def start_research(topic: str, notebook_id: Optional[str] = None) -> Dict[str, Any]:
        """Start a deep dive on `topic` in the background; returns a job id at once.

        Poll `get_job(job_id)`. The job survives a LocalBook restart. Results are
        a ranked reading list — to add one to a notebook, use `propose_note`
        (the user approves it in LocalBook).
        """
        topic = (topic or "").strip()
        if not topic:
            return {"error": "a topic is required"}
        async with audited("start_research", {"topic": topic, "notebook_id": notebook_id}):
            from services import research_jobs

            job = await asyncio.to_thread(research_jobs.create, topic[:500], notebook_id,
                                          getattr(current_caller(), "companion_id", "unknown"))
            research_jobs.launch(job["id"])          # here, on the event loop
            return {"job_id": job["id"], "status": job["status"]}

    @mcp.tool(annotations=read_only)
    async def get_job(job_id: str) -> Dict[str, Any]:
        """Status and results of a research job: queued | running | done | error."""
        async with audited("get_job", {"job_id": job_id}):
            from services import research_jobs

            job = await asyncio.to_thread(research_jobs.get, job_id)
            if not job:
                return {"error": f"no job with id {job_id}"}
            return {k: job.get(k) for k in ("id", "topic", "notebook_id", "status",
                                            "results", "error", "created_at", "updated_at")}
