"""consensus_detector — Phase 10 of v2-information-cortex.

Find topics where ≥N recently-ingested sources converge. Output drives:
  - The Curator HTML dashboard's "What's converging" cards
  - Deep-read auto-trigger (top clusters → research_engine.deep_dive)
  - Cross-notebook reach signal (notebook_counts per cluster)
  - Light agenda signal (sender_counts per cluster)

Algorithm — simple agglomerative cluster over embeddings of each event's
`payload.summary`. Chosen over BERTopic because input is small (~50-200
events per call), single-threaded, and BERTopic's HDBSCAN tuning is more
than we need.

Defensive against:
  - No events in window → empty list, no crash
  - Embedding service down → empty list (caller treats as "no consensus")
  - Malformed event payloads → skip the event, keep going
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ClusterMember(BaseModel):
    source_id: str = ""
    notebook_id: str = ""
    sender: str = ""
    summary: str = ""
    ts: str = ""
    topic_tags: List[str] = Field(default_factory=list)


class ConsensusCluster(BaseModel):
    cluster_id: str
    topic_label: str = ""
    size: int = 0
    members: List[ClusterMember] = Field(default_factory=list)
    sender_counts: Dict[str, int] = Field(default_factory=dict)
    notebook_counts: Dict[str, int] = Field(default_factory=dict)
    # The most-represented notebook — used as the deep_dive target.
    primary_notebook_id: str = ""
    newest_ts: str = ""


# ─── Helpers ──────────────────────────────────────────────────────────────


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def _embed(text: str) -> List[float]:
    if not text or not text.strip():
        return []
    try:
        from services.ollama_service import ollama_service
        result = await ollama_service.embed(text=text[:2000])
    except Exception as e:
        logger.debug(f"[consensus_detector] embed failed: {e}")
        return []
    vecs = (result or {}).get("embeddings") or []
    return list(vecs[0]) if vecs and isinstance(vecs[0], list) else []


def _centroid(vectors: List[List[float]]) -> List[float]:
    """Mean of a list of vectors. Assumes all have the same length."""
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            out[i] += x
    n = len(vectors)
    return [x / n for x in out]


def _coerce_event(e: Dict[str, Any]) -> Optional[ClusterMember]:
    """Turn a raw event row into a ClusterMember. Returns None on bad shape."""
    payload = e.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    summary = (payload.get("summary") or "").strip()
    if not summary:
        return None
    return ClusterMember(
        source_id=str(payload.get("source_id", "")),
        notebook_id=str(e.get("notebook_id", "")),
        sender=str(payload.get("sender", "")),
        summary=summary,
        ts=str(e.get("ts", "")),
        topic_tags=list(payload.get("topic_tags") or []),
    )


# ─── Public API ───────────────────────────────────────────────────────────


async def detect_consensus(
    *,
    since_days: int = 3,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.72,
    max_events: int = 300,
) -> List[ConsensusCluster]:
    """Find consensus topics across recently-ingested sources.

    Returns a list of ConsensusCluster, sorted descending by cluster size
    (ties broken by recency of newest member). Empty list when nothing
    converges (the brief's skip-digest path handles that case).
    """
    try:
        from services.curator_brain import curator_brain
    except Exception as e:
        logger.debug(f"[consensus_detector] curator_brain import failed: {e}")
        return []

    since_iso = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    raw = curator_brain.recent_events(limit=max_events, since_iso=since_iso) or []
    events = [
        m for m in (_coerce_event(e) for e in raw if (e.get("action") == "source_ingested"))
        if m is not None
    ]
    if len(events) < min_cluster_size:
        return []

    # Embed every event summary in parallel (cheap with the in-process
    # ollama_service client; we still cap total events).
    vectors = await asyncio.gather(*[_embed(m.summary) for m in events])

    # Agglomerative: walk events in original time order, assign each to
    # the nearest centroid above the threshold; else seed a new cluster.
    clusters: List[Dict[str, Any]] = []
    for member, vec in zip(events, vectors):
        if not vec:
            continue
        best_idx = -1
        best_sim = 0.0
        for i, cl in enumerate(clusters):
            sim = _cosine(vec, cl["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_idx = i
        if best_idx >= 0 and best_sim >= similarity_threshold:
            cl = clusters[best_idx]
            cl["members"].append(member)
            cl["vectors"].append(vec)
            cl["centroid"] = _centroid(cl["vectors"])
        else:
            clusters.append({
                "members": [member],
                "vectors": [vec],
                "centroid": vec,
            })

    # Keep only the converged clusters and build the public shape.
    out: List[ConsensusCluster] = []
    for i, cl in enumerate(clusters):
        if len(cl["members"]) < min_cluster_size:
            continue
        members: List[ClusterMember] = cl["members"]
        sender_counts = Counter(m.sender for m in members if m.sender)
        notebook_counts = Counter(m.notebook_id for m in members if m.notebook_id)
        # Topic label: the most-common topic_tag, else first 80 chars of newest
        # member's summary.
        tag_counts: Counter = Counter()
        for m in members:
            tag_counts.update(m.topic_tags)
        if tag_counts:
            topic_label = tag_counts.most_common(1)[0][0]
        else:
            topic_label = members[-1].summary[:80]
        newest_ts = max((m.ts for m in members), default="")
        primary_nb = notebook_counts.most_common(1)[0][0] if notebook_counts else ""
        out.append(ConsensusCluster(
            cluster_id=f"c{i}-{newest_ts[:19]}",
            topic_label=topic_label,
            size=len(members),
            members=members,
            sender_counts=dict(sender_counts),
            notebook_counts=dict(notebook_counts),
            primary_notebook_id=primary_nb,
            newest_ts=newest_ts,
        ))

    out.sort(key=lambda c: (c.size, c.newest_ts), reverse=True)
    return out
