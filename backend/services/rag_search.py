"""
RAG Search — Hybrid BM25+Vector retrieval, FlashRank reranking, and adaptive
multi-strategy search.

Extracted from rag_engine.py Phase 3. Owns all search/retrieval logic:
- Hybrid BM25 + Vector search with Reciprocal Rank Fusion
- FlashRank (or cross-encoder fallback) reranking
- Adaptive multi-strategy search (4 strategies)
- Corrective retrieval with query variants

External callers continue to use rag_engine._hybrid_search() etc. —
RAGEngine delegates here.
"""
from typing import Dict, List, Optional, Tuple

import numpy as np

from config import settings
from services import rag_query_analyzer
from services import rag_embeddings

# BM25 for hybrid search
try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False

# FlashRank for ultra-fast reranking (no torch dependency)
try:
    from flashrank import Ranker as FlashRanker, RerankRequest
    HAS_FLASHRANK = True
except ImportError:
    HAS_FLASHRANK = False


# ─── Lazy-loaded reranker state ──────────────────────────────────────────────────

_flashrank_reranker = None
_crossencoder_reranker = None


def _get_reranker():
    """Lazy load the reranker model - prefers FlashRank for speed."""
    global _flashrank_reranker, _crossencoder_reranker

    # Prefer FlashRank (ultra-fast, no torch)
    if HAS_FLASHRANK and settings.reranker_type == "flashrank":
        if _flashrank_reranker is None:
            # Use persistent cache dir (not /tmp which gets cleared on reboot)
            cache_dir = settings.data_dir / "models" / "flashrank"
            cache_dir.mkdir(parents=True, exist_ok=True)
            _flashrank_reranker = FlashRanker(
                model_name=settings.reranker_model,
                cache_dir=str(cache_dir),
                max_length=256  # Optimized for typical chunk sizes
            )
            print(f"[RAG] Loaded FlashRank reranker: {settings.reranker_model} (cache: {cache_dir})")
        return _flashrank_reranker

    # Fallback to cross-encoder (slower but works without FlashRank)
    if _crossencoder_reranker is None:
        from sentence_transformers import CrossEncoder
        reranker_model = "BAAI/bge-reranker-v2-m3"  # Cross-encoder fallback
        _crossencoder_reranker = CrossEncoder(reranker_model, max_length=512)
        print(f"[RAG] Loaded cross-encoder reranker: {reranker_model}")
    return _crossencoder_reranker


def load_reranker():
    """Force load the reranker model (used for warmup)."""
    if settings.use_reranker:
        return _get_reranker()
    return None


# ─── Hybrid Search ───────────────────────────────────────────────────────────────

def hybrid_search(
    query: str,
    table,
    query_embedding: List[float],
    k: int = 12,
) -> List[Dict]:
    """Perform hybrid search combining vector similarity and BM25 keyword matching.
    
    This dramatically improves retrieval accuracy by catching both:
    - Semantic matches (vector search): "employee performance" matches "staff evaluation"
    - Exact keyword matches (BM25): "Christopher Norman" matches documents with that exact name
    
    Uses Reciprocal Rank Fusion (RRF) to combine rankings.
    """
    # Get ALL documents for BM25 (not just vector-similar ones)
    try:
        all_docs = table.search().limit(10000).to_list()
    except Exception as e:
        print(f"[RAG] Hybrid search fallback to vector-only: {e}")
        return table.search(query_embedding).limit(k).to_list()

    if not all_docs or not HAS_BM25:
        return table.search(query_embedding).limit(k).to_list()

    # Vector search results (separate query for proper ranking)
    try:
        vector_results = table.search(query_embedding).limit(k * 2).to_list()
    except:
        vector_results = all_docs[:k * 2]

    # BM25 keyword search
    try:
        # Tokenize documents
        corpus = [doc.get("text", "").lower().split() for doc in all_docs]
        bm25 = BM25Okapi(corpus)

        # Tokenize query
        query_tokens = query.lower().split()

        # Get BM25 scores
        bm25_scores = bm25.get_scores(query_tokens)

        # Create ranked lists
        vector_ranking = {
            doc["source_id"] + str(doc.get("chunk_index", 0)): i
            for i, doc in enumerate(vector_results)
        }

        bm25_ranked_indices = np.argsort(bm25_scores)[::-1][:k * 2]
        bm25_ranking = {
            all_docs[idx]["source_id"] + str(all_docs[idx].get("chunk_index", 0)): i
            for i, idx in enumerate(bm25_ranked_indices)
        }

        # Reciprocal Rank Fusion (RRF)
        rrf_scores = {}
        rrf_k = 60  # RRF constant

        all_doc_keys = set(vector_ranking.keys()) | set(bm25_ranking.keys())

        for doc_key in all_doc_keys:
            vector_rank = vector_ranking.get(doc_key, 1000)  # Default high rank if not found
            bm25_rank = bm25_ranking.get(doc_key, 1000)

            # RRF formula: 1/(k + rank)
            rrf_scores[doc_key] = (1 / (rrf_k + vector_rank)) + (1 / (rrf_k + bm25_rank))

        # Sort by RRF score
        sorted_keys = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        # Map back to documents, preserving _distance from vector results
        doc_map = {doc["source_id"] + str(doc.get("chunk_index", 0)): doc for doc in all_docs}
        vector_dist_map = {
            doc["source_id"] + str(doc.get("chunk_index", 0)): doc.get("_distance", 200)
            for doc in vector_results
        }

        # Max RRF score for normalization (top result)
        max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0

        hybrid_results = []
        for key in sorted_keys[:k]:
            if key in doc_map:
                doc = doc_map[key].copy()
                # Preserve vector distance if available, otherwise estimate from RRF rank
                doc["_distance"] = vector_dist_map.get(key, 200)
                # Store normalized RRF score (0-1, higher is better) for confidence fallback
                doc["_rrf_score"] = rrf_scores[key] / max_rrf
                hybrid_results.append(doc)

        print(f"[RAG] Hybrid search: {len(hybrid_results)} results (vector + BM25 fusion)")
        return hybrid_results

    except Exception as e:
        print(f"[RAG] BM25 failed, using vector-only: {e}")
        return vector_results[:k]


# ─── Reranking ───────────────────────────────────────────────────────────────────

def rerank(query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
    """Rerank documents using FlashRank (preferred) or cross-encoder for better relevance."""
    if not documents:
        return documents

    reranker = _get_reranker()

    # Use FlashRank if available (ultra-fast, no torch)
    if HAS_FLASHRANK and settings.reranker_type == "flashrank":
        # FlashRank expects list of dicts with 'id' and 'text' keys
        passages = [
            {"id": i, "text": doc.get("text", ""), "meta": {"original_idx": i}}
            for i, doc in enumerate(documents)
        ]

        rerank_request = RerankRequest(query=query, passages=passages)
        results = reranker.rerank(rerank_request)

        # Map back to original documents with scores
        for result in results:
            orig_idx = result["meta"]["original_idx"]
            documents[orig_idx]["rerank_score"] = float(result["score"])

        # Sort by rerank score (higher is better) and take top_k
        ranked = sorted(documents, key=lambda x: x.get("rerank_score", 0), reverse=True)
        return ranked[:top_k]

    # Fallback to cross-encoder
    pairs = [(query, doc.get("text", "")) for doc in documents]
    scores = reranker.predict(pairs)

    for doc, score in zip(documents, scores):
        doc["rerank_score"] = float(score)

    ranked = sorted(documents, key=lambda x: x.get("rerank_score", 0), reverse=True)
    return ranked[:top_k]


# ─── Adaptive Search (unified — replaces duplicate _adaptive_search + _adaptive_search_progressive) ─

async def adaptive_search(
    table,
    question: str,
    query_embedding: List[float],
    analysis: Dict,
    top_k: int,
    return_strategies: bool = True,
) -> Tuple[List[Dict], List[str]]:
    """Adaptive search with multiple strategies and verification.
    
    Tries different search strategies until good results are found.
    Always returns (results, strategies_used) tuple.
    
    This is the SINGLE implementation — the previous duplicate
    _adaptive_search / _adaptive_search_progressive are merged here.
    """
    strategies_used = []

    # Strategy 1: Standard hybrid search with expanded query
    expanded_query = rag_query_analyzer.build_search_query(analysis, question)
    if HAS_BM25:
        results = hybrid_search(expanded_query, table, query_embedding, k=top_k * 2)
        strategies_used.append("hybrid_search")
    else:
        results = table.search(query_embedding).limit(top_k * 2).to_list()
        strategies_used.append("vector_search")

    is_good, reason = rag_query_analyzer.verify_retrieval_quality(results, analysis)

    if is_good:
        return results, strategies_used

    print(f"[RAG] Adaptive search: Strategy 1 failed - {reason}")

    # Strategy 2: Entity-focused search
    entities = analysis.get("entities") or []
    if entities:
        try:
            all_docs = table.search([0.0] * settings.embedding_dim).limit(500).to_list()
            entity_matches = []
            for doc in all_docs:
                text = doc.get("text", "").lower()
                for entity in entities:
                    if entity.lower() in text:
                        entity_matches.append(doc)
                        break

            if entity_matches:
                strategies_used.append("entity_focused")
                if HAS_BM25:
                    corpus = [doc.get("text", "").lower().split() for doc in entity_matches]
                    bm25 = BM25Okapi(corpus)
                    scores = bm25.get_scores(expanded_query.lower().split())
                    ranked_indices = np.argsort(scores)[::-1][:top_k * 2]
                    results = [entity_matches[i] for i in ranked_indices]
                else:
                    results = entity_matches[:top_k * 2]

                is_good, reason = rag_query_analyzer.verify_retrieval_quality(results, analysis)
                if is_good:
                    print("[RAG] Adaptive search: Strategy 2 (entity-focused) succeeded")
                    return results, strategies_used

                print(f"[RAG] Adaptive search: Strategy 2 failed - {reason}")
        except Exception as e:
            print(f"[RAG] Adaptive search: Strategy 2 error - {e}")

    # Strategy 3: Time-period focused search
    time_periods = analysis.get("time_periods") or []
    if time_periods:
        try:
            all_docs = table.search([0.0] * settings.embedding_dim).limit(500).to_list()
            time_matches = []
            for doc in all_docs:
                text = doc.get("text", "").lower()
                filename = doc.get("filename", "").lower()
                combined = f"{text} {filename}"
                for period in time_periods:
                    if period.lower() in combined:
                        time_matches.append(doc)
                        break

            if time_matches:
                strategies_used.append("time_focused")
                if HAS_BM25 and len(time_matches) > 1:
                    corpus = [doc.get("text", "").lower().split() for doc in time_matches]
                    bm25 = BM25Okapi(corpus)
                    scores = bm25.get_scores(expanded_query.lower().split())
                    ranked_indices = np.argsort(scores)[::-1][:top_k * 2]
                    results = [time_matches[i] for i in ranked_indices if i < len(time_matches)]
                else:
                    results = time_matches[:top_k * 2]

                is_good, reason = rag_query_analyzer.verify_retrieval_quality(results, analysis)
                if is_good:
                    print("[RAG] Adaptive search: Strategy 3 (time-focused) succeeded")
                    return results, strategies_used

                print(f"[RAG] Adaptive search: Strategy 3 failed - {reason}")
        except Exception as e:
            print(f"[RAG] Adaptive search: Strategy 3 error - {e}")

    # Strategy 4: Full-text scan with keyword matching
    try:
        all_docs = table.search([0.0] * settings.embedding_dim).limit(500).to_list()
        key_metric = analysis.get("key_metric", "")

        keyword_matches = []
        search_terms = [key_metric] if key_metric else []
        search_terms.extend(entities)
        search_terms.extend(time_periods)

        for doc in all_docs:
            text = doc.get("text", "").lower()
            match_count = sum(1 for term in search_terms if term.lower() in text)
            if match_count >= 2:  # At least 2 keywords match
                keyword_matches.append((match_count, doc))

        if keyword_matches:
            strategies_used.append("keyword_scan")
            keyword_matches.sort(key=lambda x: -x[0])
            results = [doc for _, doc in keyword_matches[:top_k * 2]]
            print(f"[RAG] Adaptive search: Strategy 4 (keyword scan) found {len(results)} matches")
            return results, strategies_used
    except Exception as e:
        print(f"[RAG] Adaptive search: Strategy 4 error - {e}")

    # Return best results we have
    print(f"[RAG] Adaptive search: All strategies tried: {strategies_used}")
    return results, strategies_used


# ─── Corrective Retrieval ────────────────────────────────────────────────────────

async def corrective_retrieval(
    table,
    question: str,
    analysis: Dict,
    top_k: int,
    original_results: List[Dict],
) -> List[Dict]:
    """Corrective retrieval using query variants when initial retrieval fails.
    
    Called when answer quality check fails. Generates variant queries and
    retrieves again to get better results.
    """
    print("[RAG] Corrective retrieval triggered - generating query variants")

    variants = await rag_query_analyzer.generate_query_variants(question)
    print(f"[RAG] Query variants: {variants}")

    all_results = list(original_results)  # Start with original
    seen_ids = {r.get('chunk_id', r.get('text', '')[:50]) for r in original_results}

    for variant in variants[1:]:  # Skip original (index 0)
        # Generate embedding for variant
        embedding = rag_embeddings.encode(variant)[0].tolist()

        # Search with variant
        if HAS_BM25:
            variant_results = hybrid_search(variant, table, embedding, k=top_k)
        else:
            variant_results = table.search(embedding).limit(top_k).to_list()

        # Add new unique results
        for r in variant_results:
            r_id = r.get('chunk_id', r.get('text', '')[:50])
            if r_id not in seen_ids:
                all_results.append(r)
                seen_ids.add(r_id)

    print(f"[RAG] Corrective retrieval found {len(all_results)} total results")
    return all_results[:top_k * 2]  # Return expanded set for reranking
