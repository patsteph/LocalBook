"""article_clusterer — Phase 2 Tier 2 (2026-06-09).

Agglomerative single-pass clustering on article embeddings to produce
labeled topic clusters for the @correspondent whats_hot deep=true intent.

Design ref: `READFIRST/CORRESPONDENT_TIER2_DESIGN.md` § B.

Approach:
  1. Pull articles with embeddings from the last `lookback_days` (default 14)
  2. Single-pass cluster by cosine similarity ≥ threshold (default 0.65)
  3. For each cluster ≥ min_size (default 3): generate a label via phi4-mini
  4. Compute recent_size (last 7d) vs baseline_size (8-14d) for hot/cold
  5. Persist to topic_clusters table

Runs on demand via `recluster_all()` — and can be wired into a nightly
job. Reading is cheap (one SQL query): `get_recent_clusters(notebook_id?)`.
"""
from __future__ import annotations

import json
import logging
import math
import struct as _struct
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_RECENT_DAYS = 7
DEFAULT_SIM_THRESHOLD = 0.65
DEFAULT_MIN_CLUSTER_SIZE = 3
MAX_LABELED_CLUSTERS = 20  # phi4-mini call cap per recluster


def _unpack_embedding(blob: bytes) -> List[float]:
    if not blob:
        return []
    n = len(blob) // 4
    if n == 0:
        return []
    try:
        return list(_struct.unpack(f"{n}f", blob))
    except Exception:
        return []


def _pack_embedding(vec: List[float]) -> bytes:
    return _struct.pack(f"{len(vec)}f", *vec)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _avg_embedding(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            out[i] += x
    inv = 1.0 / len(vectors)
    return [x * inv for x in out]


async def _label_cluster(article_titles: List[str], article_summaries: List[str]) -> str:
    """phi4-mini one-liner labeling a cluster of articles by their shared theme.
    Fallback: longest common subject token or '(unlabeled)'."""
    from services.ollama_service import ollama_service
    from config import settings

    items = []
    for i, (t, s) in enumerate(zip(article_titles[:6], article_summaries[:6])):
        items.append(f"{i+1}. {t}\n   {s}")
    user_prompt = (
        "Articles from recent newsletters, all clustered together by content similarity:\n\n"
        + "\n\n".join(items)
        + "\n\nIn 5 words or fewer, name the shared theme these articles cover. "
        "Output ONLY the theme name — no quotes, no markdown, no prefix."
    )
    try:
        result = await ollama_service.generate(
            prompt=user_prompt,
            system="You produce short topic labels. 5 words max. No quotes or punctuation.",
            model=settings.ollama_fast_model,
            temperature=0.2,
            num_predict=40,
        )
        raw = (result or {}).get("response", "").strip()
        # Strip surrounding quotes / markdown if the model adds them
        raw = raw.strip("\"'`* ")
        # Take first line only
        raw = raw.split("\n")[0].strip()[:80]
        if raw:
            return raw
    except Exception as e:
        logger.debug(f"[article_clusterer.label_cluster] phi4 failed: {e}")
    # Fallback
    return (article_titles[0] if article_titles else "(unlabeled)")[:80]


async def recluster_all(
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    min_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> int:
    """Rebuild the topic_clusters table from current article embeddings.

    Returns the number of clusters persisted (after min_size filter).
    Caller (nightly job or on-demand intent) is responsible for scheduling.
    """
    from storage.article_store import article_store
    from storage.database import get_db

    since = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    articles = await article_store.list_with_embeddings(since_iso=since, limit=5000)
    if not articles:
        logger.info("[article_clusterer] no embedded articles to cluster")
        return 0

    # Unpack
    items: List[Dict[str, Any]] = []
    for a in articles:
        vec = _unpack_embedding(a.get("embedding") or b"")
        if not vec:
            continue
        items.append({**a, "vec": vec})
    if not items:
        return 0

    # Single-pass agglomerative — for each article, find best existing
    # cluster by similarity ≥ threshold; else create new.
    clusters: List[Dict[str, Any]] = []
    for it in items:
        best_idx = -1
        best_sim = sim_threshold
        for i, c in enumerate(clusters):
            sim = _cosine(it["vec"], c["centroid"])
            if sim >= best_sim:
                best_sim = sim
                best_idx = i
        if best_idx >= 0:
            c = clusters[best_idx]
            c["items"].append(it)
            c["centroid"] = _avg_embedding([m["vec"] for m in c["items"]])
        else:
            clusters.append({"items": [it], "centroid": it["vec"]})

    # Filter by min_size
    clusters = [c for c in clusters if len(c["items"]) >= min_size]
    # Sort biggest first; cap labeling budget
    clusters.sort(key=lambda c: len(c["items"]), reverse=True)

    recent_cutoff = (datetime.utcnow() - timedelta(days=DEFAULT_RECENT_DAYS)).isoformat()
    persisted = 0
    now_iso = datetime.utcnow().isoformat()

    # Wipe prior cluster state. Could keep history as superseded_at column,
    # but for v1 we just replace — labels can drift nightly anyway.
    conn = get_db().get_connection()
    conn.execute("DELETE FROM topic_clusters")

    for idx, c in enumerate(clusters):
        members = c["items"]
        article_ids = [m["id"] for m in members]
        sender_counts: Dict[str, int] = {}
        notebook_counts: Dict[str, int] = {}
        recent_size = 0
        for m in members:
            if m.get("sender"):
                sender_counts[m["sender"]] = sender_counts.get(m["sender"], 0) + 1
            if m.get("notebook_id"):
                notebook_counts[m["notebook_id"]] = notebook_counts.get(m["notebook_id"], 0) + 1
            if m.get("created_at", "") >= recent_cutoff:
                recent_size += 1
        baseline_size = len(members) - recent_size

        # Label via phi4-mini for top N clusters; fallback for the rest
        if idx < MAX_LABELED_CLUSTERS:
            label = await _label_cluster(
                article_titles=[m.get("title") or "" for m in members],
                article_summaries=[m.get("summary") or "" for m in members],
            )
        else:
            label = (members[0].get("title") or "")[:80] or "(unlabeled)"

        avg_blob = _pack_embedding(c["centroid"])
        conn.execute(
            """INSERT INTO topic_clusters
               (id, label, article_ids, sender_counts, notebook_counts,
                avg_embedding, recent_size, baseline_size, last_built_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                label,
                json.dumps(article_ids),
                json.dumps(sender_counts),
                json.dumps(notebook_counts),
                avg_blob,
                recent_size,
                baseline_size,
                now_iso,
            ),
        )
        persisted += 1

    conn.commit()
    logger.info(f"[article_clusterer] reclustered → {persisted} clusters")
    return persisted


async def get_recent_clusters(
    *,
    notebook_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Read clusters from the table. If `notebook_id` is supplied, filter
    to clusters whose primary notebook (most-mentioned) matches.

    Per design B.3 (locked) — clustering is cross-notebook by default;
    query takes an optional notebook scope so the user can ask either
    question without a re-cluster pass.
    """
    from storage.database import get_db

    rows = get_db().get_connection().execute(
        """SELECT * FROM topic_clusters
           ORDER BY (recent_size + baseline_size) DESC
           LIMIT ?""",
        (int(limit * 3),),  # over-fetch so we have room after notebook filter
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["article_ids"] = json.loads(d.get("article_ids") or "[]")
            d["sender_counts"] = json.loads(d.get("sender_counts") or "{}")
            d["notebook_counts"] = json.loads(d.get("notebook_counts") or "{}")
        except Exception:
            continue
        if notebook_id:
            primary_nb = max(d["notebook_counts"].items(), key=lambda kv: kv[1])[0] if d["notebook_counts"] else None
            if primary_nb != notebook_id:
                continue
        # Compute delta (hot/cold signal)
        d["delta"] = int(d.get("recent_size", 0)) - int(d.get("baseline_size", 0))
        d["size"] = len(d["article_ids"])
        out.append(d)
        if len(out) >= limit:
            break
    return out
