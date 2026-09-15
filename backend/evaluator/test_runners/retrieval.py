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

TWO PATHS, because they answer different questions
--------------------------------------------------
- **adaptive** — hybrid BM25 + query expansion + FlashRank rerank. What chat actually does, so
  this is what the category SCORES.
- **vector** — `search_chunks_async`, raw nearest-neighbour. Reported alongside, because it
  isolates EMBEDDING quality from everything layered on top.

Keeping both is not redundancy, it is the difference between two diagnoses. Measured on the
22-question gold set when the `query: ` prefix landed (2026-09-14):

    vector   mean rank 6.3 → 2.0      (the embedding change, seen clearly)
    adaptive mean rank 2.5 → 1.9      (the same change, largely masked by rerank)

Read only the adaptive number and you would conclude the prefix barely mattered; read only the
vector number and you would overstate it. And a rerank or `retrieval_overcollect` change moves
the adaptive number while leaving the vector one untouched.

⚠️ This split is also a trap I walked into on 2026-09-14: recovered content sat at vector ranks
7-13 and I reported a ranking problem. On the adaptive path 3 of those 4 were already top-5.
**The scored number is the adaptive one. The vector number is a diagnostic, not a verdict.**
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


async def _search_adaptive(rag_engine, table, question: str, top_k: int):
    """Retrieve the way chat does — expansion + hybrid + rerank — without generating.

    Mirrors the opening of `rag_engine.query`: expand, embed AS A QUERY, then adaptive_search.
    The analysis dict is the minimum `adaptive_search` reads; the full LLM query analysis is
    deliberately skipped so this measures RETRIEVAL rather than the analyzer's mood, and stays
    comparable across models.
    """
    expanded = rag_engine._expand_query(question)
    embedding = (await rag_engine.encode_async(expanded, is_query=True))[0].tolist()
    analysis = {"intent": "factual", "keywords": [], "expanded_query": expanded}
    return await rag_engine._adaptive_search(table, question, embedding, analysis, top_k) or []


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
            # The SCORED path: what chat does — INCLUDING its real top_k.
            #
            # `adaptive_search` derives its candidate pool from top_k (hybrid fetches k*2 before
            # reranking), so the parameter changes the retrieval, not just how much of it you
            # see. Passing the harness's deep 20 made the reranker order 40 candidates instead
            # of 10 and measurably WORSENED the top of the list — nDCG@10 0.507 vs 0.718 —
            # i.e. the harness scored a configuration the app never runs. Use the app's value.
            app_top_k = getattr(settings, "retrieval_top_k", 5)
            try:
                table = rag_engine._get_table(notebook_id)
                chunks = await _search_adaptive(rag_engine, table, question, app_top_k)
            except Exception as _ae:
                print(f"[EVAL-RETRIEVAL] adaptive path unavailable ({_ae}) — vector only")
                chunks = await rag_engine.search_chunks_async(notebook_id, question,
                                                             top_k=_DEEP_K)
            elapsed = (time.time() - start) * 1000
            result.total_time_ms = elapsed
            result.input_chars = len(question)

            relevance = rm.relevance_from_markers(chunks or [], markers)
            rank = rm.first_relevant_rank(relevance)

            # The DIAGNOSTIC path: same question, embeddings only. A gold chunk that the vector
            # path ranks 14th and rerank lifts to 2nd tells you the embedding is weak and the
            # reranker is carrying it — which is invisible from either number alone.
            try:
                vec = await rag_engine.search_chunks_async(notebook_id, question, top_k=_DEEP_K)
                vec_rel = rm.relevance_from_markers(vec or [], markers)
                vector_rank = rm.first_relevant_rank(vec_rel)
                vector_ndcg = round(rm.ndcg_at_k(vec_rel, 10), 4)
            except Exception:
                vector_rank, vector_ndcg = None, None

            # Three outcomes, and the middle one needs BOTH paths to detect.
            #
            # Scoring on the app's real top_k means the adaptive path returns ~5 results, so it
            # can no longer tell "at rank 11" from "absent" on its own. The vector path, fetched
            # deep, supplies that: found there but not surfaced here means the text is indexed
            # and findable, and the loss is in ranking or the top-k cut. That is a different
            # (and usually cheaper) fix than an embedding that cannot find it at all, so
            # collapsing the two into a single zero would throw away the actionable half.
            if rank is not None:
                result.overall_score = _SCORE_TOP5
                result.passed = True
            elif vector_rank is not None:
                result.overall_score = _SCORE_TOP20
                result.passed = True
                result.failure_reason = (
                    f"not in the app's top {app_top_k}, but vector search finds it at rank "
                    f"{vector_rank} — indexed and findable; the loss is ranking, not retrieval"
                )
            else:
                result.overall_score = 0
                result.passed = False
                result.failure_reason = (
                    f"no gold chunk in either path (adaptive top {app_top_k}, vector top "
                    f"{_DEEP_K}) — the text is in the index but nothing surfaces it"
                )

            result.sub_scores = {
                "hit_at_5": rm.hit_at_k(relevance, 5),
                "hit_at_20": rm.hit_at_k(relevance, _DEEP_K),
                "ndcg_at_10": round(rm.ndcg_at_k(relevance, 10), 4),
                "mrr": round(rm.mrr(relevance), 4),
                "first_relevant_rank": rank,
                "chunks_returned": len(chunks or []),
                "latency_ms": round(elapsed, 1),
                # Diagnostic only — never scored. See the module docstring.
                "vector_rank": vector_rank,
                "vector_ndcg_at_10": vector_ndcg,
                "rerank_lift": (
                    (vector_rank - rank) if (vector_rank and rank) else None
                ),
            }
            result.actual_output_preview = (
                f"rank={rank} of {len(chunks or [])} | ndcg@10="
                f"{result.sub_scores['ndcg_at_10']:.3f} | vector rank={vector_rank}"
            )
            print(f"[EVAL-RETRIEVAL] {qid}: rank={rank} (vector {vector_rank}) "
                  f"score={result.overall_score} "
                  f"ndcg@10={result.sub_scores['ndcg_at_10']:.3f} ({elapsed:.0f}ms)")

        except Exception as e:
            result.passed = False
            result.overall_score = 0
            result.failure_reason = str(e)[:200]
            print(f"[EVAL-RETRIEVAL] {qid} FAILED: {e}")

        results.append(result)

    scored = [r for r in results if not r.skipped]
    if scored:
        n = len(scored)
        hits5 = sum(r.sub_scores.get("hit_at_5", 0) for r in scored)
        hits20 = sum(r.sub_scores.get("hit_at_20", 0) for r in scored)
        ndcg = sum(r.sub_scores.get("ndcg_at_10", 0) for r in scored) / n
        ranks = [r.sub_scores.get("first_relevant_rank") for r in scored]
        mean_rank = sum(x or 99 for x in ranks) / n
        print(f"[EVAL-RETRIEVAL] ADAPTIVE (scored): recall@5={hits5:.0f}/{n} "
              f"recall@20={hits20:.0f}/{n} nDCG@10={ndcg:.3f} mean-rank={mean_rank:.1f}")
        vranks = [r.sub_scores.get("vector_rank") for r in scored]
        if any(v is not None for v in vranks):
            vmean = sum(v or 99 for v in vranks) / n
            vndcg = sum(r.sub_scores.get("vector_ndcg_at_10") or 0 for r in scored) / n
            print(f"[EVAL-RETRIEVAL] vector (diagnostic): nDCG@10={vndcg:.3f} "
                  f"mean-rank={vmean:.1f}  — gap to adaptive shows what rerank is carrying")
    return results
