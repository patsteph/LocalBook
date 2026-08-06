"""Canvas Sub-topics — STABLE, accretive sub-topic assignment (Canvas evolution, Phase 2).

The old populate re-clustered from scratch every run (connected-components), so topic identity and
positions thrashed. This replaces that with an ACCRETIVE model: sub-topics have persisted identity +
an embedding centroid (`canvas_topics_store`). On each Populate we assign every thread to the NEAREST
EXISTING sub-topic (cosine ≥ threshold); threads that fit no existing topic and cluster together seed a
NEW sub-topic; a lone unmatched thread stays an ORPHAN (topic_id=None → an invitation on the map). No
global re-cluster → the same threads land in the same cards run over run.

Pure core (`cluster`) is CI-unit-tested with hand-made vectors; the async orchestrator does the
embedding + the fast-model title/synthesis + persistence, and never raises.
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tunable (arctic-1024 cosine). Assigning to an EXISTING topic is a slightly lower bar than seeding a
# brand-new one (existing identity is worth preserving); a new topic needs ≥2 threads to exist at all
# (a single unmatched thread is an orphan, not a topic).
ASSIGN_THRESHOLD = 0.58
SEED_THRESHOLD = 0.66
MIN_NEW_TOPIC_SIZE = 2


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _avg(vecs: List[List[float]]) -> List[float]:
    vecs = [v for v in vecs if v]
    if not vecs:
        return []
    dim = len(vecs[0])
    out = [0.0] * dim
    n = 0
    for v in vecs:
        if len(v) != dim:
            continue
        n += 1
        for i, x in enumerate(v):
            out[i] += x
    if n == 0:
        return []
    return [x / n for x in out]


def cluster(node_vecs: List[List[float]], existing_topics: List[Dict[str, Any]],
            assign_thr: float = ASSIGN_THRESHOLD, seed_thr: float = SEED_THRESHOLD,
            min_new: int = MIN_NEW_TOPIC_SIZE) -> List[Dict[str, Any]]:
    """Accretive assignment. Returns the surviving topics, each:
    `{id (existing id or None for new), centroid, members: [node_idx], is_new}`. Nodes not in any
    returned topic's `members` are ORPHANS. Existing topics keep their id even if membership shifts;
    an existing topic that loses ALL members is dropped."""
    topics = [{"id": t.get("id"), "centroid": list(t.get("centroid") or []), "members": [],
               "is_new": False} for t in existing_topics]

    # Pass 1 — assign each embedded node to the nearest EXISTING topic.
    unassigned: List[int] = []
    for i, v in enumerate(node_vecs):
        if not v:
            continue  # no embedding → orphan
        best, best_sim = None, assign_thr
        for t in topics:
            if not t["centroid"]:
                continue
            s = _cosine(v, t["centroid"])
            if s >= best_sim:
                best_sim, best = s, t
        if best is not None:
            best["members"].append(i)
        else:
            unassigned.append(i)

    # Pass 2 — cluster the leftovers among themselves (single-pass running centroid) → new topics.
    new_topics: List[Dict[str, Any]] = []
    for i in unassigned:
        v = node_vecs[i]
        best, best_sim = None, seed_thr
        for t in new_topics:
            s = _cosine(v, t["centroid"])
            if s >= best_sim:
                best_sim, best = s, t
        if best is not None:
            best["members"].append(i)
            best["centroid"] = _avg([node_vecs[j] for j in best["members"]])
        else:
            new_topics.append({"id": None, "centroid": list(v), "members": [i], "is_new": True})

    # Recompute centroids from current members; drop empties; a NEW topic needs ≥ min_new members
    # (a lone leftover stays an orphan).
    final: List[Dict[str, Any]] = []
    for t in topics + new_topics:
        if not t["members"]:
            continue
        if t["is_new"] and len(t["members"]) < min_new:
            continue  # single unmatched thread → orphan, not a topic
        t["centroid"] = _avg([node_vecs[j] for j in t["members"]])
        final.append(t)
    return final


# ── Title + one-line synthesis (adapted from community_detection.generate_community_summary) ──
async def _title_synthesis(member_titles: List[str]) -> Tuple[str, str]:
    titles = [t for t in member_titles if t][:12]
    fallback = (titles[0] if titles else "Topic")[:40]
    if not titles:
        return fallback, ""
    try:
        from services.ollama_service import ollama_service
        from config import settings
        listed = "\n".join(f"- {t}" for t in titles)
        prompt = (
            "These related items are things a user explored or created while learning. Give a SHORT "
            "topic name and a one-line summary of the through-line.\n\n"
            f"Items:\n{listed}\n\n"
            "Respond EXACTLY as:\nNAME: <3-5 word topic name>\nSUMMARY: <one sentence>"
        )
        res = await ollama_service.generate(
            prompt=prompt, model=settings.ollama_fast_model, temperature=0.3,
            num_predict=80, think=False, timeout=20.0)
        text = (res or {}).get("response", "") or ""
        name, summary = fallback, ""
        for line in text.splitlines():
            ls = line.strip()
            if ls.upper().startswith("NAME:"):
                name = ls.split(":", 1)[1].strip().strip('"') or fallback
            elif ls.upper().startswith("SUMMARY:"):
                summary = ls.split(":", 1)[1].strip().strip('"')
        return name[:60], summary[:200]
    except Exception as e:
        logger.debug(f"[canvas_subtopics] title synthesis skipped: {type(e).__name__}: {e}")
        return fallback, ""


async def reassign_one(notebook_id: str, node: Dict[str, Any],
                       extra_text: str = "") -> Dict[str, Any]:
    """P4 — re-embed ONE thread (its snapshot text + the user's elicited intent) and try to JOIN the
    nearest existing sub-topic (cosine ≥ ASSIGN_THRESHOLD). On a match, fold the thread into that
    topic's running-mean centroid + bump member_count. Returns
    `{"topic_id": str|None, "suggestions": [{"id","title","score"}]}` (top-3 nearest topics).

    A single thread cannot SEED a topic (that needs ≥ MIN_NEW_TOPIC_SIZE members), so a no-match
    thread stays an orphan — the suggestions still guide the user. Never raises."""
    from storage import canvas_topics_store as topics_store
    from services.canvas_populate import snapshot_text
    out: Dict[str, Any] = {"topic_id": None, "suggestions": []}
    try:
        text = f"{snapshot_text(node)}\n{extra_text}".strip()
        if not text:
            return out
        from services.ollama_service import ollama_service
        vecs = await ollama_service.embed_batch([text])
        vec = list(vecs[0]) if vecs and vecs[0] else []
        if not vec:
            return out

        topics = topics_store.list_topics(notebook_id)
        ranked = sorted(
            ({"id": t["id"], "title": t.get("title", ""), "synthesis": t.get("synthesis", ""),
              "centroid": t.get("centroid") or [], "member_count": int(t.get("member_count") or 0),
              "score": _cosine(vec, t.get("centroid") or [])}
             for t in topics if t.get("centroid")),
            key=lambda r: r["score"], reverse=True)
        out["suggestions"] = [{"id": r["id"], "title": r["title"], "score": round(r["score"], 4)}
                              for r in ranked[:3]]

        if ranked and ranked[0]["score"] >= ASSIGN_THRESHOLD:
            best = ranked[0]
            n, old = best["member_count"], best["centroid"]
            # Incremental running mean — the thread was an orphan (not a member), so adding one is
            # exactly the new average of (n existing members + this one).
            if old and len(old) == len(vec) and n > 0:
                new_centroid = [(old[i] * n + vec[i]) / (n + 1) for i in range(len(vec))]
            else:
                new_centroid = old or vec
            topics_store.upsert_topic(notebook_id, {
                "id": best["id"], "title": best["title"], "synthesis": best["synthesis"],
                "centroid": new_centroid, "member_count": n + 1})
            out["topic_id"] = best["id"]
        return out
    except Exception as e:
        logger.warning(f"[canvas_subtopics] reassign_one failed ({notebook_id}): {e}")
        return out


async def assign_and_persist(notebook_id: str, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Embed each node, assign to a stable sub-topic (accretive), persist topics, and stamp
    `node['topic_id']` in place (None = orphan). Returns the surviving topics (with `member_ids` +
    geometry from the store) for layout. Never raises — on any failure, nodes keep topic_id=None."""
    from services.canvas_populate import snapshot_text
    from storage import canvas_topics_store as topics_store
    try:
        texts = [snapshot_text(n) for n in nodes]
        vecs: List[List[float]] = []
        try:
            from services.ollama_service import ollama_service
            vecs = await ollama_service.embed_batch(texts)
        except Exception as e:
            logger.warning(f"[canvas_subtopics] embedding failed ({notebook_id}): {e}")
            return []
        vecs = [list(v) if v else [] for v in (vecs or [])]
        if len(vecs) != len(nodes):
            return []

        existing = topics_store.list_topics(notebook_id)
        existing_by_id = {t["id"]: t for t in existing}
        result = cluster(vecs, existing)

        surviving: List[Dict[str, Any]] = []
        keep_ids: List[str] = []
        for t in result:
            members = t["members"]
            member_titles = [nodes[i].get("title", "") for i in members]
            if t["is_new"] or not t.get("id"):
                tid = str(uuid.uuid4())
                title, synthesis = await _title_synthesis(member_titles)
                geom = {}
            else:
                tid = t["id"]
                prev = existing_by_id.get(tid, {})
                title, synthesis = prev.get("title", ""), prev.get("synthesis", "")
                if not title:  # heal a topic that never got a title
                    title, synthesis = await _title_synthesis(member_titles)
                geom = {"x": prev.get("x"), "y": prev.get("y"),
                        "width": prev.get("width"), "height": prev.get("height"),
                        "collapsed": 1 if prev.get("collapsed", True) else 0}
            for i in members:
                nodes[i]["topic_id"] = tid
            payload = {"id": tid, "title": title, "synthesis": synthesis,
                       "centroid": t["centroid"], "member_count": len(members)}
            payload.update({k: v for k, v in geom.items() if v is not None})
            topics_store.upsert_topic(notebook_id, payload)
            keep_ids.append(tid)
            surviving.append({"id": tid, "title": title, "synthesis": synthesis,
                              "member_ids": [nodes[i].get("id") for i in members],
                              "member_count": len(members)})
        topics_store.delete_missing(notebook_id, keep_ids)
        n_orphan = sum(1 for n in nodes if not n.get("topic_id"))
        logger.info(f"[canvas_subtopics] nb={notebook_id}: {len(surviving)} sub-topics, "
                    f"{n_orphan} orphans over {len(nodes)} threads")
        return surviving
    except Exception as e:
        logger.warning(f"[canvas_subtopics] assign_and_persist failed ({notebook_id}): {e}")
        return []
