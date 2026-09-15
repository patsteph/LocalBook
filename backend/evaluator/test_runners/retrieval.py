"""Retrieval-quality test runner — measures the RANKING, not the answer.

Every other runner that touches retrieval judges it through a generated answer, so retrieval
quality arrives mixed with prompt fit, sampling noise and judge variance. This one stops at the
ranking: it asks the same question the app asks, looks at what came back, and reports where the
known-correct chunk landed. No generation, no judge, no model-dependent formatting — so the same
number means the same thing across models, which is what makes an embedding or rerank change
measurable at all.

This is the harness the 2026-08-21 research identified as the blocker for four verified-real RAG
improvements (`query: ` prefix, overcollect floor, PDF page metadata, unembedded notes). None of
them could ship because nothing could tell whether they helped.

Gold pairs: `test_fixtures/retrieval_gold.json` (committed, built against the corpus that ships)
merged with `<data_dir>/eval/retrieval_gold.local.json` if present — the local overlay is how
real field questions get added without putting notebook content in a public repo.

⚠️ SCOPE — what this measures, precisely
----------------------------------------
`search_chunks_async` is **pure vector search**: no hybrid BM25, no query expansion, no
FlashRank rerank. That is deliberate for a first cut — it isolates EMBEDDING quality from
everything layered on top, which is exactly what the `query: ` prefix and unembedded-notes
changes affect, and it has no model-dependent behaviour to confound a cross-model comparison.

But it is NOT the path chat uses. `rag_search.adaptive_search` is, and a rerank or
`retrieval_overcollect` change will NOT show up here. Adding a second, adaptive-path variant is
the obvious next step; it needs `table` / `query_embedding` / `analysis` built the way
`rag_engine.query` builds them, so it wants a live index to validate against rather than being
written blind. Until then, read this category as "embedding + vector retrieval", which is what
its scores honestly describe.
"""
import json
import time
from datetime import datetime
from pathlib import Path

from evaluator.models import EvalResult
from evaluator import retrieval_metrics as rm

# Retrieve deep, score shallow. The app shows the top 5, but fetching 20 tells us WHERE a miss
# happened: absent entirely (embedding failure) vs present at rank 11 (a ranking/rerank problem).
# Those need completely different fixes, and hit@5 alone cannot tell them apart.
_DEEP_K = 20
_SHALLOW_K = 5

# Rank 6-20 still earns partial credit: the chunk WAS retrievable, and the loss is in ranking or
# in the top-k cut. Scoring it zero would hide the difference between "cannot find it" and
# "finds it but buries it" — the second is usually a one-line fix.
_SCORE_TOP5 = 100
_SCORE_TOP20 = 60


def _load_gold() -> list:
    """Committed pairs + the optional local overlay (real questions, kept out of git)."""
    here = Path(__file__).resolve().parents[1] / "test_fixtures" / "retrieval_gold.json"
    questions = []
    try:
        questions = list(json.loads(here.read_text()).get("questions") or [])
    except Exception as e:
        print(f"[EVAL-RETRIEVAL] could not read committed gold set: {e}")

    try:
        from config import settings
        local = Path(settings.data_dir) / "eval" / "retrieval_gold.local.json"
        if local.exists():
            extra = list(json.loads(local.read_text()).get("questions") or [])
            questions.extend(extra)
            print(f"[EVAL-RETRIEVAL] +{len(extra)} local gold pairs from {local}")
    except Exception as e:
        print(f"[EVAL-RETRIEVAL] local gold overlay skipped ({e})")
    return questions


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list:
    """One EvalResult per gold question."""
    from config import settings
    from services.rag_engine import rag_engine

    gold = _load_gold()
    if not gold:
        skipped = EvalResult(
            test_id="retrieval_empty",
            category="retrieval",
            test_name="Retrieval: (no gold pairs)",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        skipped.mark_skipped("No retrieval gold pairs available")
        return [skipped]

    results = []
    for case in gold:
        qid = case.get("id") or "unnamed"
        question = (case.get("question") or "").strip()
        markers = case.get("markers") or []

        result = EvalResult(
            test_id=f"retrieval_{qid}",
            category="retrieval",
            test_name=f"Retrieval: {qid}",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        result.stamp_provider(settings.embedding_model)

        if not question or not markers:
            result.mark_skipped("case needs a question and at least one marker")
            results.append(result)
            continue

        try:
            start = time.time()
            chunks = await rag_engine.search_chunks_async(notebook_id, question, top_k=_DEEP_K)
            elapsed = (time.time() - start) * 1000
            result.total_time_ms = elapsed
            result.input_chars = len(question)

            relevance = rm.relevance_from_markers(chunks or [], markers)
            rank = rm.first_relevant_rank(relevance)

            if rank is not None and rank <= _SHALLOW_K:
                result.overall_score = _SCORE_TOP5
                result.passed = True
            elif rank is not None:
                result.overall_score = _SCORE_TOP20
                result.passed = True
                result.failure_reason = (
                    f"found at rank {rank} — retrievable, but below the top {_SHALLOW_K} the "
                    f"app actually uses"
                )
            else:
                result.overall_score = 0
                result.passed = False
                result.failure_reason = (
                    f"no gold chunk in the top {_DEEP_K} — the text is in the index but "
                    f"retrieval never surfaced it"
                )

            result.sub_scores = {
                "hit_at_5": rm.hit_at_k(relevance, 5),
                "hit_at_20": rm.hit_at_k(relevance, _DEEP_K),
                "ndcg_at_10": round(rm.ndcg_at_k(relevance, 10), 4),
                "mrr": round(rm.mrr(relevance), 4),
                "first_relevant_rank": rank,
                "chunks_returned": len(chunks or []),
                "latency_ms": round(elapsed, 1),
            }
            result.actual_output_preview = (
                f"rank={rank} of {len(chunks or [])} | ndcg@10="
                f"{result.sub_scores['ndcg_at_10']:.3f}"
            )
            print(f"[EVAL-RETRIEVAL] {qid}: rank={rank} score={result.overall_score} "
                  f"ndcg@10={result.sub_scores['ndcg_at_10']:.3f} ({elapsed:.0f}ms)")

        except Exception as e:
            result.passed = False
            result.overall_score = 0
            result.failure_reason = str(e)[:200]
            print(f"[EVAL-RETRIEVAL] {qid} FAILED: {e}")

        results.append(result)

    scored = [r for r in results if not r.skipped]
    if scored:
        hits5 = sum(r.sub_scores.get("hit_at_5", 0) for r in scored)
        hits20 = sum(r.sub_scores.get("hit_at_20", 0) for r in scored)
        ndcg = sum(r.sub_scores.get("ndcg_at_10", 0) for r in scored) / len(scored)
        print(f"[EVAL-RETRIEVAL] recall@5={hits5}/{len(scored)} "
              f"recall@20={hits20}/{len(scored)} mean nDCG@10={ndcg:.3f}")
    return results
