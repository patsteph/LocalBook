"""The memory bridge: a companion reads and writes LocalBook's memory (LB-4).

    prefetch      → what LocalBook remembers that bears on `query`, in a budget
    sync_turn     → record a user/assistant exchange; extract facts later
    session_end   → summarise the session into an archival checkpoint

One shared memory (user decision 2026-09-30): prefetch reads LocalBook's own
memories as well as the companion's, and LocalBook's chat reads what companions
wrote. Every companion write is tagged `companion:<id>` (storage/companion_memory)
and removable in one action.

The LLM work — fact extraction and session summaries — goes on the enrichment
worker's queue on the FAST model, never inline and never as a stray
`create_task`: a companion must not be able to put the main model under load, or
jump ahead of the user.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from storage import companion_memory as cm

logger = logging.getLogger(__name__)

_IMPORTANCE_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_SESSION_TURNS = 6
_STOPWORDS = {"the", "and", "that", "this", "with", "what", "when", "where", "which",
              "about", "have", "from", "your", "were", "there", "their", "would",
              "could", "should", "does", "into"}


def _tag(conversation_or_source: str) -> str:
    s = str(conversation_or_source or "")
    return ":".join(s.split(":")[:2]) if s.startswith(cm.PREFIX) else "localbook"


def _terms(query: str, n: int = 3) -> List[str]:
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{3,}", query or "")}
    words -= _STOPWORDS
    return sorted(words, key=len, reverse=True)[:n]


class _Budget:
    def __init__(self, chars: int):
        self.left = max(0, int(chars))

    def take(self, text: str) -> bool:
        if len(text) + 1 > self.left:
            return False
        self.left -= len(text) + 1
        return True


async def prefetch(companion_id: str, query: str, session_id: str, k: int = 8,
                   char_budget: int = 2000) -> Dict[str, Any]:
    """Ranked memory for `query`, trimmed to `char_budget` characters.

    Order is also priority when the budget runs out: core facts (by importance),
    then archival memories (hybrid vector + BM25, every shared namespace), then
    this session's recent turns, then older turns that mention the query's terms.
    """
    from storage.memory_store import memory_store

    cm.check_ids(companion_id, session_id)
    k = max(1, min(int(k), 50))
    budget = _Budget(char_budget)
    sections: Dict[str, List[Dict[str, Any]]] = {"core": [], "archival": [], "recall": []}

    core = sorted(memory_store.load_core_memory().entries,
                  key=lambda e: _IMPORTANCE_ORDER.get(getattr(e.importance, "value", e.importance), 9))
    for e in core:
        text = f"{e.key}: {e.value}"
        if budget.take(text):
            sections["core"].append({"text": text, "importance": getattr(e.importance, "value", e.importance),
                                     "source": _tag(e.source_conversation_id)})

    if query and query.strip():
        for r in await memory_store.search_archival_memory_async(query=query, limit=k):
            text = r.entry.content
            if budget.take(text):
                sections["archival"].append({"text": text, "score": round(r.combined_score, 3),
                                             "source": _tag(r.entry.source_id)})

    seen = set()
    for turn in reversed(cm.session_turns(companion_id, session_id, _SESSION_TURNS)):
        text = f"[{turn['role']}] {turn['content']}"
        seen.add((turn["role"], turn["content"]))
        if budget.take(text):
            sections["recall"].append({"text": text, "ts": turn["ts"], "source": f"{cm.PREFIX}{companion_id}"})

    hits = []
    for term in _terms(query):
        hits.extend(memory_store.search_recall_memory(term, limit=k))
    for entry in sorted(hits, key=lambda e: e.timestamp, reverse=True):
        if (entry.role, entry.content) in seen:
            continue
        seen.add((entry.role, entry.content))
        text = f"[{entry.role}] {entry.content}"
        if len(sections["recall"]) >= _SESSION_TURNS + k:
            break
        if budget.take(text):
            sections["recall"].append({"text": text, "ts": entry.timestamp.isoformat(),
                                       "source": _tag(entry.conversation_id)})

    lines = [s["text"] for part in ("core", "archival", "recall") for s in sections[part]]
    return {"sections": sections, "text": "\n".join(lines),
            "chars": max(0, int(char_budget)) - budget.left}


def sync_turn(companion_id: str, session_id: str, user: str, assistant: str, ts,
              source: str = "") -> Dict[str, Any]:
    """Record one exchange. Idempotent on (session_id, ts): a retry stores nothing
    and queues nothing. Extraction runs later on the enrichment worker."""
    cm.check_ids(companion_id, session_id)
    new_user = bool(user and user.strip()) and cm.record_turn(companion_id, session_id, "user", user, ts)
    new_assistant = bool(assistant and assistant.strip()) and cm.record_turn(
        companion_id, session_id, "assistant", assistant, ts)
    queued = False
    if new_user:
        queued = _enqueue_extraction(companion_id, session_id, user, str(ts))
    return {"stored": {"user": new_user, "assistant": new_assistant}, "extraction_queued": queued,
            "source": source or None}


def _enqueue_extraction(companion_id: str, session_id: str, text: str, ts: str) -> bool:
    from models.memory import MemoryExtractionRequest
    from services.enrichment_jobs import EnrichmentJob, JobTier
    from services.enrichment_worker import enrichment_worker
    from services.memory_agent import memory_agent

    request = MemoryExtractionRequest(message=text, role="user",
                                      conversation_id=cm.conversation_id(companion_id, session_id))
    ns = cm.namespace(companion_id)
    enrichment_worker.enqueue(EnrichmentJob(
        key=f"mem-extract:{companion_id}:{session_id}:{ts}",
        tier=JobTier.DAYDREAM,
        factory=lambda: memory_agent.extract_memories(request, store_recall=False, namespace=ns),
        label=f"memory extraction ({companion_id})",
    ))
    return True


def session_end(companion_id: str, session_id: str) -> Dict[str, Any]:
    """Queue the session's summary. Nothing to do for an empty or one-turn session."""
    from services.enrichment_jobs import EnrichmentJob, JobTier
    from services.enrichment_worker import enrichment_worker
    from services.memory_agent import memory_agent
    from storage.memory_store import memory_store

    conv = cm.conversation_id(companion_id, session_id)
    entries = memory_store.get_conversation(conv)
    if len(entries) < 2:
        return {"queued": False, "turns": len(entries)}
    ns = cm.namespace(companion_id)

    async def _compact():
        fresh = memory_store.get_conversation(conv)
        if len(fresh) >= 2:
            await memory_agent.checkpoint_conversation(conv, fresh, namespace=ns)

    enrichment_worker.enqueue(EnrichmentJob(
        key=f"mem-session-end:{companion_id}:{session_id}",
        tier=JobTier.DAYDREAM,
        factory=_compact,
        label=f"session summary ({companion_id})",
    ))
    return {"queued": True, "turns": len(entries)}
