"""
RAG Context — Citation building, confidence scoring, and context assembly.

Extracted from rag_engine.py Phase 5. Owns all post-retrieval context
construction: confidence scoring, citation filtering, parent text expansion,
and numbered context building.

External callers continue to use rag_engine._build_citations_and_context() —
RAGEngine delegates here.
"""
import re
from typing import Dict, List, Set, Tuple

from storage.source_store import source_store

# Pattern matching the "YouTube Video {id}" fallback title
_YT_FALLBACK_RE = re.compile(r'^YouTube Video ([A-Za-z0-9_-]{8,15})$')
# Cache so we only attempt oEmbed once per video_id per process lifetime
_yt_title_cache: Dict[str, str] = {}


async def _try_fix_youtube_title(filename: str, source_id: str, source_data: Dict) -> str:
    """If filename matches the YouTube Video fallback pattern, try oEmbed to get the real title.
    
    Updates source_store on success so the fix is permanent.
    Returns the best available title.
    """
    m = _YT_FALLBACK_RE.match(filename)
    if not m:
        return filename
    
    video_id = m.group(1)
    
    # Check process-level cache first
    if video_id in _yt_title_cache:
        return _yt_title_cache[video_id]
    
    try:
        import httpx
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(oembed_url)
            if response.status_code == 200:
                real_title = response.json().get("title")
                if real_title and real_title != filename:
                    # Persist the fix
                    notebook_id = source_data.get("notebook_id", "")
                    if notebook_id:
                        await source_store.update(notebook_id, source_id, {"filename": real_title})
                        print(f"[RAG] Fixed YouTube title: '{filename}' → '{real_title}'")
                    _yt_title_cache[video_id] = real_title
                    return real_title
    except Exception:
        pass
    
    # Cache the failure so we don't retry every query
    _yt_title_cache[video_id] = filename
    return filename


async def build_citations_and_context(
    results: List[Dict],
    log_prefix: str = "[RAG]",
) -> Tuple[List[Dict], Set[str], str, bool]:
    """Build citations and context from search results.
    
    Returns: (citations, sources_set, context_string, low_confidence)
    """
    # Get source filenames for citations
    source_filenames = {}
    for result in results:
        sid = result["source_id"]
        if sid not in source_filenames:
            source_data = await source_store.get(sid)
            filename = source_data.get("filename", "Unknown") if source_data else "Unknown"
            # Lazy-fix YouTube fallback titles
            filename = await _try_fix_youtube_title(filename, sid, source_data or {})
            source_filenames[sid] = filename

    # Build citations from search results
    all_citations = []
    for i, result in enumerate(results):
        text = result.get("text", "")
        
        # Use rerank_score if available AND meaningful, otherwise use vector distance
        rerank_score = result.get("rerank_score")
        distance = result.get("_distance", 100.0)
        
        # FlashRank returns very low scores (< 0.01) for holistic/thematic queries
        # In those cases, fall back to vector distance which better captures semantic similarity
        use_rerank = rerank_score is not None and rerank_score > 0.01
        
        if use_rerank:
            # FlashRank returns scores 0-1 (0 = irrelevant, 1 = highly relevant)
            # Cross-encoder returns scores roughly -10 to +10
            if rerank_score <= 1.0:
                # FlashRank: boost scores since it's conservative
                # 0.1 → 0.24, 0.3 → 0.52, 0.5 → 0.80
                confidence = min(1.0, rerank_score * 1.4 + 0.1)
            else:
                # Cross-encoder fallback: scores are -10 to +10
                confidence = max(0, min(1, (rerank_score + 5) / 10))
            
            print(f"{log_prefix} Citation {i+1}: rerank_score={rerank_score:.3f} -> confidence={confidence:.0%}")
        else:
            # FlashRank returned low scores - use hybrid of vector distance and RRF score
            # RRF score captures both semantic (vector) and keyword (BM25) relevance
            rrf_score = result.get("_rrf_score", 0)
            
            if rrf_score > 0:
                # Use RRF score (already normalized 0-1, higher is better)
                # Scale to reasonable confidence range: top result ~85%, lower results differentiated
                confidence = 0.5 + (rrf_score * 0.4)  # Range: 50-90%
                print(f"{log_prefix} Citation {i+1}: rerank={rerank_score if rerank_score is not None else 'None'} low, using RRF={rrf_score:.2f} -> confidence={confidence:.0%}")
            else:
                # Pure vector distance fallback
                # 0 dist = 100%, 50 dist = 88%, 100 dist = 75%, 200 dist = 50%, 400+ dist = 0%
                confidence = max(0, min(1, 1 - (distance / 400)))
                if rerank_score is not None:
                    print(f"{log_prefix} Citation {i+1}: rerank={rerank_score:.4f} low, using distance={distance:.2f} -> confidence={confidence:.0%}")
                else:
                    print(f"{log_prefix} Citation {i+1}: distance={distance:.2f} -> confidence={confidence:.0%}")
        
        confidence_level = "high" if confidence >= 0.6 else "medium" if confidence >= 0.4 else "low"
        
        all_citations.append({
            "number": i + 1,
            "source_id": result.get("source_id", "unknown"),
            "filename": source_filenames.get(result.get("source_id", ""), "Unknown"),
            "chunk_index": result.get("chunk_index", 0),
            "text": text,
            "parent_text": result.get("parent_text", ""),  # v0.60: Parent document context
            "snippet": text[:150] + "..." if len(text) > 150 else text,
            "page": result.get("metadata", {}).get("page") if isinstance(result.get("metadata"), dict) else None,
            "confidence": round(confidence, 2),
            "confidence_level": confidence_level
        })

    # Only filter out truly irrelevant results (< 20% confidence)
    # Lowered from 25% because L2 distances can be high even for relevant results
    # The reranker will handle fine-grained relevance if enabled
    quality_citations = [c for c in all_citations if c["confidence"] >= 0.20]
    
    # Check if ALL citations are very low confidence (< 10%) - this means we have no relevant sources
    max_confidence = max((c["confidence"] for c in all_citations), default=0)
    very_low_confidence = max_confidence < 0.10
    
    # If filtering removed everything but we have some decent sources, keep top 3
    if len(quality_citations) == 0 and len(all_citations) > 0 and not very_low_confidence:
        quality_citations = all_citations[:3]
        print(f"{log_prefix} Low confidence fallback: using top 3 citations")
    elif very_low_confidence:
        # All sources are essentially irrelevant - don't use any
        print(f"{log_prefix} VERY LOW CONFIDENCE: max={max_confidence:.0%}, refusing to use sources")
        quality_citations = []
    
    print(f"{log_prefix} Citations: {len(quality_citations)} used (from {len(all_citations)} found, max_conf={max_confidence:.0%})")
    
    # Renumber citations after filtering
    sources = set()
    for i, citation in enumerate(quality_citations):
        citation["number"] = i + 1
        sources.add(citation["source_id"])
    
    # Build numbered context
    # v0.60: Use parent_text for expanded context if available
    numbered_context = []
    for i, c in enumerate(quality_citations):
        # Prefer parent_text for richer context, fall back to text
        context_text = c.get('parent_text') or c.get('text', '')
        numbered_context.append(f"[{i+1}] {context_text}")
    context = "\n\n".join(numbered_context)
    
    # Mark as low confidence if no quality citations OR all sources are very low
    low_confidence = len(quality_citations) == 0 or very_low_confidence
    
    return quality_citations, sources, context, low_confidence
