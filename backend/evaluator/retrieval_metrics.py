"""Retrieval-level metrics — recall@k, nDCG@k, MRR. Pure functions, no I/O, no model.

Why this exists (2026-09-14). Retrieval quality has never been measured directly. The only
signal was `score_context_recall`: two labelled questions, binary hit/miss, folded into an
answer-quality score alongside faithfulness and speed. That cannot resolve a retrieval change —
the 2026-08-21 research reached exactly this conclusion, and four verified-real RAG improvements
have been unshippable ever since because nothing could tell whether they helped:

  - the missing `query: ` prefix on the embedding path (arctic is trained asymmetrically)
  - `retrieval_overcollect = 12` starving FlashRank under query decomposition
  - PDF page structure parsed then discarded, so citations cannot say "p. 12"
  - `note_store` / `canvas_notes` notes never embedded at all

An answer-level LLM judge cannot see any of these through gemma's sampling noise. Retrieval
metrics can, because they compare a RANKING against a known-correct answer and involve no
generation at all. The live run on 2026-09-14 makes the point: run-to-run variance moved the
overall score by more than a deliberate scoring change did.

These are the standard IR definitions, kept pure so they can be unit-tested in CI with no model,
no index, and no network — the numbers have to be trustworthy before anything is built on them.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence


def hit_at_k(relevance: Sequence[float], k: int) -> float:
    """1.0 if ANY relevant item appears in the top k, else 0.0.

    With any-of gold markers there is one conceptual right answer that may live in several
    chunks, so "did we find it" is the honest question — recall against a total of 1.
    """
    if k <= 0:
        return 0.0
    return 1.0 if any(r > 0 for r in relevance[:k]) else 0.0


def recall_at_k(relevance: Sequence[float], k: int, total_relevant: Optional[int] = None) -> float:
    """Fraction of all relevant items retrieved in the top k.

    `total_relevant` defaults to the number of relevant items present in the ranking, which
    makes this recall over WHAT WAS RETRIEVED. Pass the true total when it is known — otherwise
    a system that retrieved 1 of 5 relevant chunks and ranked it first scores a perfect 1.0.
    """
    if k <= 0:
        return 0.0
    found = sum(1 for r in relevance[:k] if r > 0)
    total = total_relevant if total_relevant is not None else sum(1 for r in relevance if r > 0)
    if not total:
        return 0.0
    return min(1.0, found / total)


def dcg_at_k(relevance: Sequence[float], k: int) -> float:
    """Discounted cumulative gain. Log2 discount, rank 1 undiscounted."""
    return sum(
        (rel / math.log2(i + 2))
        for i, rel in enumerate(relevance[:k])
        if rel
    )


def ndcg_at_k(relevance: Sequence[float], k: int,
              ideal: Optional[Sequence[float]] = None) -> float:
    """Normalized DCG — DCG against the best possible ordering of the same gains.

    Unlike hit@k, this rewards ranking the right chunk FIRST rather than fifth, which is what
    actually matters downstream: the context builder has a token budget, so a gold chunk at
    rank 5 may never reach the model even though retrieval "found" it.

    `ideal` lets the caller supply the true relevance multiset when more relevant items exist
    than were retrieved; otherwise the retrieved gains are used, sorted descending.
    """
    if k <= 0:
        return 0.0
    actual = dcg_at_k(relevance, k)
    best_gains = sorted(ideal if ideal is not None else relevance, reverse=True)
    best = dcg_at_k(best_gains, k)
    if best <= 0:
        return 0.0
    return actual / best


def mrr(relevance: Sequence[float]) -> float:
    """Reciprocal rank of the FIRST relevant item (1.0 = rank 1). 0.0 if none."""
    for i, rel in enumerate(relevance):
        if rel > 0:
            return 1.0 / (i + 1)
    return 0.0


def first_relevant_rank(relevance: Sequence[float]) -> Optional[int]:
    """1-based rank of the first relevant item, or None. The most human-readable diagnostic:
    'the answer was at rank 9' says more than 'nDCG 0.31'."""
    for i, rel in enumerate(relevance):
        if rel > 0:
            return i + 1
    return None


def relevance_from_markers(chunks: Sequence[dict], markers: Sequence[str]) -> List[float]:
    """Map retrieved chunks → binary relevance by any-of marker matching.

    Mirrors `scoring.score_context_recall`: a marker phrase may be split across a chunk
    boundary, so `parent_text` and `snippet` are searched alongside `text`. Matching the
    enclosing paragraph is a legitimate retrieval, not a near-miss.
    """
    markers_lower = [m.lower() for m in (markers or []) if m]
    if not markers_lower:
        return [0.0] * len(chunks)
    out: List[float] = []
    for c in chunks or []:
        haystack = " ".join([
            (c.get("text", "") or ""),
            (c.get("parent_text", "") or ""),
            (c.get("snippet", "") or ""),
        ]).lower()
        out.append(1.0 if any(m in haystack for m in markers_lower) else 0.0)
    return out
