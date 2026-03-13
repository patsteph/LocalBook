"""RAG (Retrieval Augmented Generation) Engine

Implements hybrid search (BM25 + Vector) for maximum retrieval accuracy.
Vector search captures semantic similarity, BM25 captures exact keyword matches.
"""
import asyncio
import os
import re
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional, AsyncGenerator, Tuple, Union

import httpx
import lancedb
import numpy as np

from config import settings
# v0.6.5: ConceptExtractionRequest no longer needed - using BERTopic
from models.memory import MemoryExtractionRequest
from services.memory_agent import memory_agent
from storage.source_store import source_store

# v1.0.3: RAG metrics and caching for performance monitoring
from services.rag_metrics import rag_metrics, RAGStage
from services.rag_cache import embedding_cache, answer_cache
from services.web_fallback import web_fallback
from services.query_decomposer import query_decomposer
from services.entity_extractor import entity_extractor
from services.source_router import source_router
from services.entity_graph import entity_graph
from services.community_detection import community_detector
from services import rag_query_analyzer
from services import rag_chunking
from services import rag_generation
from services import rag_embeddings
from services import rag_llm
from services import rag_search
from services import rag_context
from services import rag_verification
from services import rag_storage

# BM25 for hybrid search
try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
    print("[RAG] ✓ BM25 hybrid search enabled")
except ImportError as e:
    HAS_BM25 = False
    print(f"[RAG] ⚠ BM25 not available ({e}), falling back to vector-only search")

# FlashRank for ultra-fast reranking (no torch dependency)
try:
    from flashrank import Ranker as FlashRanker, RerankRequest
    HAS_FLASHRANK = True
    print("[RAG] ✓ FlashRank reranker enabled (ultra-fast, CPU)")
except ImportError as e:
    HAS_FLASHRANK = False
    print(f"[RAG] ⚠ FlashRank not available ({e}), will use cross-encoder fallback")

_concept_extraction_semaphore = asyncio.Semaphore(int(os.getenv("LOCALBOOK_KG_CONCURRENCY", "4")))  # Increased from 2 to 4

# Note: Debouncing removed - we now await extraction directly in ingest_document

# Shared thread pool for LanceDB operations (avoids creating per-query)
_search_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lancedb_search")


class RAGEngine:
    """RAG engine for document Q&A"""

    def __init__(self):
        self.db_path = settings.db_path
        self.embedding_model = None  # Lazy load (for sentence-transformers fallback)
        self.reranker = None  # Lazy load reranker
        self.flashrank_reranker = None  # FlashRank reranker (preferred)
        self.db = None
        self._use_ollama_embeddings = settings.use_ollama_embeddings
        self._use_reranker = settings.use_reranker
        self._query_pattern_cache = {}  # Cache for common query patterns
    
    def _get_reranker(self):
        """Lazy load the reranker model - prefers FlashRank for speed"""
        return rag_search._get_reranker()
    
    def _load_reranker(self):
        """Force load the reranker model (used for warmup)"""
        return rag_search.load_reranker()
    
    def _hybrid_search(
        self, 
        query: str, 
        table, 
        query_embedding: List[float], 
        k: int = 12
    ) -> List[Dict]:
        """Perform hybrid search combining vector similarity and BM25 keyword matching.
        
        Uses Reciprocal Rank Fusion (RRF) to combine rankings.
        """
        return rag_search.hybrid_search(query, table, query_embedding, k=k)
    
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """Rerank documents using FlashRank (preferred) or cross-encoder for better relevance"""
        return rag_search.rerank(query, documents, top_k=top_k)
    
    def _load_embedding_model(self):
        """Force load the embedding model (used for warmup)"""
        return rag_embeddings.load_embedding_model()
    
    def _get_embedding_model(self):
        """Lazy load embedding model (for sentence-transformers fallback)"""
        return rag_embeddings.get_embedding_model()
    
    def encode(self, texts: Union[str, List[str]]) -> np.ndarray:
        """Encode texts to embeddings (compatible with SentenceTransformer interface)"""
        return rag_embeddings.encode(texts)
    
    async def encode_async(self, texts: Union[str, List[str]]) -> np.ndarray:
        """Async encode texts to embeddings using parallel processing.
        
        Use this instead of encode() in async contexts for 10-20x speedup.
        """
        return await rag_embeddings.encode_async(texts)

    def _get_stored_vector_dim(self, table) -> Optional[int]:
        """Get the dimension of vectors stored in a table from schema"""
        return rag_storage.get_stored_vector_dim(table)

    def _get_table(self, notebook_id: str):
        """Get or create LanceDB table for notebook"""
        return rag_storage.get_table(notebook_id)
    
    def _table_has_parent_text(self, table) -> bool:
        """Check if table schema includes parent_text column"""
        return rag_storage.table_has_parent_text(table)

    async def _build_citations_and_context(
        self, 
        results: List[Dict], 
        log_prefix: str = "[RAG]"
    ) -> Tuple[List[Dict], set, str, bool]:
        """Build citations and context from search results.
        
        Returns: (citations, sources_set, context_string, low_confidence)
        """
        return await rag_context.build_citations_and_context(results, log_prefix)

    async def ingest_document(
        self,
        notebook_id: str,
        source_id: str,
        text: str,
        filename: str = "Unknown",
        source_type: str = "document"
    ) -> Dict:
        """Ingest a document into the RAG system"""
        return await rag_storage.ingest_document(notebook_id, source_id, text, filename, source_type)

    async def append_to_document(
        self,
        notebook_id: str,
        source_id: str,
        text: str,
        chunk_prefix: str = ""
    ) -> Dict:
        """Append additional content to an existing source's index."""
        return await rag_storage.append_to_document(notebook_id, source_id, text, chunk_prefix)

    async def delete_source(self, notebook_id: str, source_id: str) -> bool:
        """Delete all chunks for a source from LanceDB."""
        return await rag_storage.delete_source(notebook_id, source_id)

    async def _generate_document_summary(self, text: str, filename: str, source_type: str) -> Optional[str]:
        """Generate a summary of the document at ingestion time."""
        return await rag_storage.generate_document_summary(text, filename, source_type)

    async def _add_to_topic_model(
        self,
        notebook_id: str,
        source_id: str,
        chunks: List[str],
        embeddings: np.ndarray
    ):
        """Add document chunks to BERTopic model for topic discovery."""
        await rag_storage._add_to_topic_model(notebook_id, source_id, chunks, embeddings)

    def search_chunks(self, notebook_id: str, query_text: str, top_k: int = 5) -> List[Dict]:
        """Search for relevant chunks in a notebook's vector store."""
        return rag_storage.search_chunks(notebook_id, query_text, top_k)

    async def query(
        self,
        notebook_id: str,
        question: str,
        source_ids: Optional[List[str]] = None,
        top_k: int = 4,
        enable_web_search: bool = False,
        llm_provider: Optional[str] = None
    ) -> Dict:
        """Query the RAG system (non-streaming)"""
        total_start = time.time()
        query_id = str(uuid.uuid4())
        query_type = self._classify_query(question)
        
        # v1.0.3: Start metrics tracking
        rag_metrics.start_query(query_id, notebook_id, question, query_type)
        
        print(f"\n{'='*60}")
        print(f"[RAG] Starting query: '{question[:50]}...'")
        print(f"{'='*60}")

        # Step 1: PARALLEL query analysis + embedding (0ms added latency)
        rag_metrics.start_stage(RAGStage.QUERY_ANALYSIS)
        step_start = time.time()
        
        # Check query pattern cache first
        cache_key = question.lower().strip()[:100]
        cached_analysis = self._query_pattern_cache.get(cache_key)
        query_cache_hit = cached_analysis is not None
        rag_metrics.record_cache_hit("query", query_cache_hit)
        
        if cached_analysis:
            query_analysis = cached_analysis
        else:
            analysis_task = asyncio.create_task(self._analyze_query_with_llm(question))
        
        # Generate embedding with cache
        rag_metrics.start_stage(RAGStage.EMBEDDING)
        basic_expanded = self._expand_query(question)
        
        # v1.0.3: Use embedding cache
        cached_emb = embedding_cache.get(basic_expanded)
        if cached_emb is not None:
            query_embedding = cached_emb
            rag_metrics.record_cache_hit("embedding", True)
        else:
            query_embedding = self.encode(basic_expanded)[0].tolist()
            embedding_cache.put(basic_expanded, query_embedding)
            rag_metrics.record_cache_hit("embedding", False)
        rag_metrics.end_stage(RAGStage.EMBEDDING)
        
        if not cached_analysis:
            query_analysis = await analysis_task
            self._query_pattern_cache[cache_key] = query_analysis
            if len(self._query_pattern_cache) > 100:
                keys = list(self._query_pattern_cache.keys())
                for k in keys[:20]:
                    del self._query_pattern_cache[k]
        rag_metrics.end_stage(RAGStage.QUERY_ANALYSIS)
        
        print(f"[RAG] Step 1 - Parallel Analysis+Embedding: {time.time() - step_start:.2f}s")
        
        # v1.0.3: Check answer cache EARLY (before expensive search)
        cached_answer = await answer_cache.get(question, notebook_id, query_embedding)
        if cached_answer is not None:
            rag_metrics.record_cache_hit("answer", True)
            total_time = (time.time() - total_start) * 1000
            await rag_metrics.end_query(total_time)
            print(f"[RAG] ⚡ Answer cache hit ({cached_answer.get('cache_type', 'unknown')}) - {total_time:.0f}ms")
            return {
                "answer": cached_answer["answer"],
                "citations": cached_answer["citations"],
                "sources": list(set(c.get("source_id", "") for c in cached_answer["citations"])),
                "web_sources": None,
                "follow_up_questions": [],
                "low_confidence": False,
                "cache_hit": True
            }
        rag_metrics.record_cache_hit("answer", False)
        
        # Build optimized search query
        expanded_query = self._build_search_query(query_analysis, question)
        expanded_query = self._expand_query(expanded_query)

        # Step 2: Get table and check for data
        table = self._get_table(notebook_id)
        try:
            if table.count_rows() == 0:
                return {
                    "answer": "I don't have any documents to search yet. Please upload some documents first.",
                    "citations": [], "sources": [], "web_sources": None,
                    "follow_up_questions": [], "low_confidence": True
                }
        except Exception:
            pass

        # Step 2b: Query decomposition for complex queries
        is_complex, complexity_type = query_decomposer.is_complex_query(question)
        sub_questions = None
        if is_complex:
            sub_questions = await query_decomposer.decompose(question)
            if len(sub_questions) > 1:
                print(f"[RAG] Query decomposed into {len(sub_questions)} sub-questions ({complexity_type})")

        # Step 3: Adaptive search with multiple strategies
        rag_metrics.start_stage(RAGStage.VECTOR_SEARCH)
        step_start = time.time()
        overcollect_k = settings.retrieval_overcollect if self._use_reranker else top_k
        
        try:
            if sub_questions and len(sub_questions) > 1:
                # Search all sub-questions in PARALLEL and merge results
                per_query_k = max(2, overcollect_k // len(sub_questions))
                
                # Pre-compute embeddings (cache-aware)
                sub_embeddings = []
                for sub_q in sub_questions:
                    sub_emb = embedding_cache.get(sub_q)
                    if sub_emb is None:
                        sub_emb = self.encode(sub_q)[0].tolist()
                        embedding_cache.put(sub_q, sub_emb)
                    sub_embeddings.append(sub_emb)
                
                # Run all sub-query searches concurrently
                search_tasks = [
                    self._adaptive_search(table, sub_q, sub_emb, query_analysis, per_query_k)
                    for sub_q, sub_emb in zip(sub_questions, sub_embeddings)
                ]
                sub_results_list = await asyncio.gather(*search_tasks, return_exceptions=True)
                
                # Merge and dedup
                all_results = []
                seen_ids = set()
                for sub_results in sub_results_list:
                    if isinstance(sub_results, Exception):
                        print(f"[RAG] Sub-query search failed: {sub_results}")
                        continue
                    for r in sub_results:
                        r_id = r.get('chunk_id', hash(r.get('text', '')[:100]))
                        if r_id not in seen_ids:
                            all_results.append(r)
                            seen_ids.add(r_id)
                
                results = all_results
                print(f"[RAG] Merged {len(results)} results from {len(sub_questions)} parallel sub-queries")
            else:
                results = await self._adaptive_search(
                    table, question, query_embedding, query_analysis, overcollect_k
                )
            
            rag_metrics.end_stage(RAGStage.VECTOR_SEARCH)
            print(f"[RAG] Step 2 - Adaptive Search ({len(results)} results): {time.time() - step_start:.2f}s")
        except Exception as e:
            rag_metrics.record_error(str(e), RAGStage.VECTOR_SEARCH)
            await rag_metrics.end_query((time.time() - total_start) * 1000)
            print(f"Search error: {e}")
            return {
                "answer": "I encountered an error searching your documents.",
                "citations": [], "sources": [], "web_sources": None,
                "follow_up_questions": [], "low_confidence": True
            }

        # Filter by source_ids if specified
        if source_ids:
            results = [r for r in results if r["source_id"] in source_ids]

        # Step 3b: Rerank
        if self._use_reranker and len(results) > top_k:
            rag_metrics.start_stage(RAGStage.RERANKING)
            step_start = time.time()
            results = self.rerank(question, results, top_k=top_k + 1)
            rag_metrics.end_stage(RAGStage.RERANKING)
            print(f"[RAG] Step 3 - Reranking ({len(results)} results): {time.time() - step_start:.2f}s")

        # Step 3c: Entity-aware boost
        # Boost results that contain entities mentioned in the query
        results = entity_extractor.boost_results_by_entity(notebook_id, question, results)
        
        # Get entity context for LLM prompt enhancement
        entity_context = entity_extractor.get_entity_context_for_query(notebook_id, question)

        # Step 3d: Source-type routing boost
        # Boost tabular sources for numeric queries, text sources for explanatory queries
        routing_decision = source_router.route(question)
        results = source_router.apply_routing_boost(results, routing_decision)

        # Step 4: Build citations and context (shared helper)
        rag_metrics.start_stage(RAGStage.CONTEXT_BUILD)
        step_start = time.time()
        citations, sources, context, low_confidence = await self._build_citations_and_context(results, "[RAG]")
        num_citations = len(citations)
        
        # v1.0.3: Record retrieval metrics
        confidences = [c.get("confidence", 0) for c in citations]
        max_conf = max(confidences) if confidences else 0
        avg_conf = sum(confidences) / len(confidences) if confidences else 0
        rag_metrics.record_retrieval(
            chunks_retrieved=len(results),
            chunks_after_rerank=len(results),
            citations_used=num_citations,
            sources_used=len(sources),
            max_confidence=max_conf,
            avg_confidence=avg_conf,
            low_confidence=low_confidence
        )
        
        # v1.0.3: Build context from chunks (NO compression - let LLM see full data)
        # Previous compression was causing data loss on specific queries
        chunk_texts = [c.get("text", "") for c in citations]
        context = "\n\n".join(f"[{i+1}] {text}" for i, text in enumerate(chunk_texts))
        
        # v1.0.3: Append (not prepend) supplementary context to avoid overshadowing source data
        supplementary = []
        
        if entity_context:
            supplementary.append(f"Entity background: {entity_context[:500]}")
        
        # v1.0.4: Add graph relationship context for connected entity queries
        found_entities = entity_extractor.find_entities_in_query(notebook_id, question)
        if found_entities:
            entity_names = [e.name for e in found_entities]
            graph_context = entity_graph.get_context_for_query(notebook_id, entity_names)
            if graph_context:
                supplementary.append(f"Related entities: {graph_context[:500]}")
            
            # v1.0.5: Add community context for holistic queries (Phase 3)
            if community_detector.is_holistic_query(question):
                community_context = community_detector.get_community_context(notebook_id, entity_names)
                if community_context:
                    supplementary.append(f"Topic overview: {community_context[:500]}")
        
        # Append supplementary at the end so source data comes first
        if supplementary:
            context += "\n\n---\nAdditional context:\n" + "\n".join(supplementary)
            print(f"[RAG] Added {len(supplementary)} supplementary context sections")
        
        rag_metrics.end_stage(RAGStage.CONTEXT_BUILD)
        print(f"[RAG] Step 4 - Build context: {time.time() - step_start:.2f}s")

        # Step 4b: If query was decomposed, structure context with sub-question awareness
        if sub_questions and len(sub_questions) > 1:
            decomposed_note = query_decomposer.build_decomposed_prompt(
                question, sub_questions, ["(see sources above)"] * len(sub_questions)
            )
            context = f"{context}\n\n---\nQuery structure:\n{decomposed_note}"
            print(f"[RAG] Injected decomposed synthesis structure ({len(sub_questions)} sub-questions)")

        # Step 5: Generate answer
        rag_metrics.start_stage(RAGStage.LLM_GENERATION)
        step_start = time.time()
        conversation_id = str(uuid.uuid4())
        answer_result = await self._generate_answer(question, context, num_citations, llm_provider, notebook_id, conversation_id)
        answer = answer_result["answer"]
        memory_used = answer_result.get("memory_used", [])
        memory_context_summary = answer_result.get("memory_context_summary")
        rag_metrics.end_stage(RAGStage.LLM_GENERATION)
        print(f"[RAG] Step 5 - LLM answer: {time.time() - step_start:.2f}s")
        
        # Step 5b: Quality check + corrective retrieval if needed
        rag_metrics.start_stage(RAGStage.QUALITY_CHECK)
        quality_ok, quality_reason = self._check_answer_quality(question, answer, query_type)
        rag_metrics.record_quality_check(quality_ok, quality_reason)
        rag_metrics.end_stage(RAGStage.QUALITY_CHECK)
        
        if not quality_ok:
            print(f"[RAG] Quality check failed: {quality_reason}")
            rag_metrics.start_stage(RAGStage.CORRECTIVE_RETRIEVAL)
            rag_metrics.record_corrective_retrieval(True)
            step_start = time.time()
            
            # Corrective retrieval with query variants
            corrected_results = await self._corrective_retrieval(
                table, question, query_analysis, overcollect_k, results
            )
            
            # Re-rerank if we have new results
            if len(corrected_results) > len(results):
                if self._use_reranker:
                    corrected_results = self.rerank(question, corrected_results, top_k=top_k + 1)
                
                # Rebuild context with new results
                citations, sources, context, low_confidence = await self._build_citations_and_context(
                    corrected_results, "[RAG CORRECTIVE]"
                )
                num_citations = len(citations)
                
                # Regenerate answer with better context
                answer_result = await self._generate_answer(
                    question, context, num_citations, llm_provider, notebook_id, conversation_id
                )
                answer = answer_result["answer"]
                print(f"[RAG] Step 5b - Corrective retrieval + re-answer: {time.time() - step_start:.2f}s")
            rag_metrics.end_stage(RAGStage.CORRECTIVE_RETRIEVAL)
        else:
            rag_metrics.record_corrective_retrieval(False)
            print("[RAG] Quality check passed")

        # Step 5c: Web search fallback if confidence still too low
        web_sources = None
        if enable_web_search or (low_confidence and max_conf < 0.25):
            should_fallback, fallback_reason = web_fallback.should_use_web_fallback(
                max_confidence=max_conf,
                citations_count=num_citations,
                low_confidence_flag=low_confidence
            )
            
            if should_fallback:
                print(f"[RAG] Triggering web fallback (reason: {fallback_reason})")
                step_start = time.time()
                
                try:
                    web_context, web_sources = await web_fallback.get_web_context(question)
                    
                    if web_context:
                        # Combine local and web context
                        combined_context = context
                        if combined_context:
                            combined_context += "\n\n--- WEB SOURCES ---\n\n" + web_context
                        else:
                            combined_context = web_context
                        
                        # Regenerate answer with web context
                        answer_result = await self._generate_answer(
                            question, combined_context, num_citations + len(web_sources),
                            llm_provider, notebook_id, conversation_id
                        )
                        answer = answer_result["answer"]
                        low_confidence = False  # Web augmented, no longer low confidence
                        print(f"[RAG] Step 5c - Web fallback ({len(web_sources)} sources): {time.time() - step_start:.2f}s")
                except Exception as e:
                    print(f"[RAG] Web fallback error (continuing without): {e}")

        # Step 6: Generate follow-up questions
        rag_metrics.start_stage(RAGStage.FOLLOWUP_GENERATION)
        step_start = time.time()
        follow_up_questions = await self._generate_follow_up_questions_fast(question, context)
        rag_metrics.end_stage(RAGStage.FOLLOWUP_GENERATION)
        print(f"[RAG] Step 6 - Follow-ups: {time.time() - step_start:.2f}s")

        # Step 7: Memory extraction (fire-and-forget)
        async def _extract_memories_background():
            try:
                await memory_agent.extract_memories(MemoryExtractionRequest(
                    message=question, role="user",
                    conversation_id=conversation_id, notebook_id=notebook_id
                ))
                await memory_agent.extract_memories(MemoryExtractionRequest(
                    message=answer, role="assistant",
                    conversation_id=conversation_id, notebook_id=notebook_id, context=question
                ))
            except Exception as e:
                print(f"[RAG] Memory extraction failed (non-fatal): {e}")
        
        asyncio.create_task(_extract_memories_background())

        # v1.0.3: Cache this answer for future similar queries
        await answer_cache.put(question, notebook_id, query_embedding, answer, citations)

        # v1.0.3: Finalize metrics
        total_time_ms = (time.time() - total_start) * 1000
        await rag_metrics.end_query(total_time_ms)
        
        print(f"{'='*60}")
        print(f"[RAG] TOTAL query time: {total_time_ms/1000:.2f}s")
        print(f"{'='*60}\n")

        return {
            "answer": answer,
            "citations": citations,
            "sources": list(sources),
            "web_sources": web_sources,
            "follow_up_questions": follow_up_questions,
            "low_confidence": low_confidence,
            "memory_used": memory_used,
            "memory_context_summary": memory_context_summary
        }

    # =========================================================================
    # Phase 2: Retrieval Quality Improvements
    # =========================================================================
    
    async def _analyze_query_with_llm(self, question: str) -> Dict:
        """Use LLM to dynamically analyze query and extract search terms.
        
        This replaces brittle regex patterns with intelligent query understanding.
        Returns a dict with: search_terms, entities, time_periods, data_type
        """
        try:
            prompt = f"""Analyze this question and extract search information. Output ONLY valid JSON.

Question: {question}

Extract:
1. "search_terms": List of key terms to search for (include variations, e.g., "Chris" -> ["Chris", "Christopher"])
2. "entities": Names of people, companies, or specific items mentioned
3. "time_periods": Any dates, quarters (Q1, Q2), years, or fiscal years mentioned (e.g., "Q1 2026" -> "Q 1 FY 2026")
4. "data_type": What kind of data is being asked for ("count", "comparison", "list", "explanation", "summary")
5. "key_metric": The specific metric being asked about (e.g., "demos", "revenue", "meetings")

JSON:"""
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "temperature": 0}
                    }
                )
                result = response.json().get("response", "{}")
                # Extract JSON from response
                import json
                # Find JSON in response
                start = result.find("{")
                end = result.rfind("}") + 1
                if start >= 0 and end > start:
                    analysis = json.loads(result[start:end])
                    # Sanitize: LLM can return null for any field — guard all expected keys
                    analysis["search_terms"] = analysis.get("search_terms") or []
                    analysis["entities"] = analysis.get("entities") or []
                    analysis["time_periods"] = analysis.get("time_periods") or []
                    analysis["data_type"] = analysis.get("data_type") or "explanation"
                    analysis["key_metric"] = analysis.get("key_metric") or ""
                    print(f"[RAG] LLM Query Analysis: {analysis}")
                    return analysis
        except Exception as e:
            print(f"[RAG] LLM query analysis failed: {e}, using fallback")
        
        # Fallback to basic extraction
        return self._fallback_query_analysis(question)
    
    def _fallback_query_analysis(self, question: str) -> Dict:
        """Fallback query analysis when LLM is unavailable."""
        return rag_query_analyzer.fallback_query_analysis(question)
    
    def _build_search_query(self, analysis: Dict, original_question: str) -> str:
        """Build an optimized search query from LLM analysis."""
        return rag_query_analyzer.build_search_query(analysis, original_question)
    
    def _expand_query(self, question: str) -> str:
        """Phase 2.1: Query expansion with synonyms and related terms."""
        return rag_query_analyzer.expand_query(question)
    
    def _extract_entities(self, question: str) -> List[str]:
        """Phase 2.2: Extract named entities from query for better matching."""
        return rag_query_analyzer.extract_entities(question)
    
    def _boost_entity_matches(self, results: List[Dict], entities: List[str]) -> List[Dict]:
        """Phase 2.2: Boost results containing extracted entities."""
        return rag_query_analyzer.boost_entity_matches(results, entities)
    
    def _extract_temporal_filter(self, question: str) -> Optional[Dict]:
        """Phase 2.4: Extract temporal references from query for filtering."""
        return rag_query_analyzer.extract_temporal_filter(question)
    
    def _boost_temporal_relevance(self, results: List[Dict], temporal_filter: Dict) -> List[Dict]:
        """Phase 2.4: Boost results that match temporal criteria."""
        return rag_query_analyzer.boost_temporal_relevance(results, temporal_filter)
    
    def _ensure_source_diversity(self, results: List[Dict], min_sources: int = 2) -> List[Dict]:
        """Phase 2.3: Ensure results come from multiple sources when possible."""
        return rag_query_analyzer.ensure_source_diversity(results, min_sources)
    
    def _verify_retrieval_quality(self, results: List[Dict], analysis: Dict) -> Tuple[bool, str]:
        """Verify that retrieved chunks actually contain relevant data."""
        return rag_query_analyzer.verify_retrieval_quality(results, analysis)
    
    async def _adaptive_search(self, table, question: str, query_embedding: List[float], 
                                analysis: Dict, top_k: int) -> List[Dict]:
        """Adaptive search with multiple strategies and verification."""
        results, _ = await rag_search.adaptive_search(table, question, query_embedding, analysis, top_k)
        return results
    
    async def _adaptive_search_progressive(self, table, question: str, query_embedding: List[float], 
                                           analysis: Dict, top_k: int) -> Tuple[List[Dict], List[str]]:
        """Adaptive search that returns strategies tried for progressive UI."""
        return await rag_search.adaptive_search(table, question, query_embedding, analysis, top_k)
    
    def _classify_query(self, question: str) -> str:
        """Classify query type for optimal prompt and model selection.
        
        Returns: 'factual', 'synthesis', or 'complex'
        """
        return rag_query_analyzer.classify_query(question)
    
    def _detect_response_format(self, question: str) -> str:
        """Detect the ideal response format from the query — pure regex, zero latency."""
        return rag_query_analyzer.detect_response_format(question)

    def _should_auto_upgrade_to_think(self, question: str) -> bool:
        """Invisible auto-routing: detect if a 'fast' query should be upgraded to 'think' mode."""
        return rag_query_analyzer.should_auto_upgrade_to_think(question)
    
    def _check_answer_quality(self, question: str, answer: str, query_type: str) -> Tuple[bool, str]:
        """Lightweight quality check for answers - no LLM call, just heuristics."""
        return rag_query_analyzer.check_answer_quality(question, answer, query_type)
    
    async def _generate_query_variants(self, question: str) -> List[str]:
        """Generate variant queries to improve retrieval on retry."""
        return rag_query_analyzer.generate_query_variants(question)
    
    async def _corrective_retrieval(self, table, question: str, analysis: Dict, 
                                     top_k: int, original_results: List[Dict]) -> List[Dict]:
        """Corrective retrieval using query variants when initial retrieval fails."""
        return await rag_search.corrective_retrieval(table, question, analysis, top_k, original_results)
    
    def _get_prompt_for_query_type(self, query_type: str, num_citations: int, avg_confidence: float = 0.5) -> str:
        """Get optimized prompt based on query classification."""
        return rag_generation.get_prompt_for_query_type(query_type, num_citations, avg_confidence)

    def _extract_mentioned_sources(self, question: str, notebook_id: str) -> List[str]:
        """Extract source IDs if the user mentions specific filenames in their query."""
        return rag_query_analyzer.extract_mentioned_sources(question, notebook_id)

    async def query_stream(
        self,
        notebook_id: str,
        question: str,
        source_ids: Optional[List[str]] = None,
        top_k: int = 4,
        llm_provider: Optional[str] = None,
        deep_think: bool = False
    ) -> AsyncGenerator[Dict, None]:
        """Query the RAG system with streaming response"""
        total_start = time.time()
        query_id = str(uuid.uuid4())
        query_type = self._classify_query(question)
        
        # v1.0.6: Start metrics tracking for streaming queries
        rag_metrics.start_query(query_id, notebook_id, question, query_type)
        
        # Auto-detect if user mentions specific source files
        if not source_ids:
            mentioned_sources = self._extract_mentioned_sources(question, notebook_id)
            if mentioned_sources:
                source_ids = mentioned_sources
                print(f"[RAG STREAM] Auto-filtering to mentioned sources: {source_ids}")
        
        # Auto-routing: upgrade fast queries to think mode if they're complex
        auto_upgraded = False
        if not deep_think and self._should_auto_upgrade_to_think(question):
            deep_think = True
            auto_upgraded = True
        
        mode_str = " [DEEP THINK]" if deep_think else " [FAST]"
        if auto_upgraded:
            mode_str += " (auto-upgraded)"
        print(f"\n{'='*60}")
        print(f"[RAG STREAM{mode_str}] Starting query: '{question[:50]}...'")
        print(f"{'='*60}")

        # Notify frontend of the actual mode being used (especially for auto-upgrades)
        yield {
            "type": "mode",
            "deep_think": deep_think,
            "auto_upgraded": auto_upgraded
        }

        # Immediate status so user sees feedback within milliseconds
        yield {"type": "status", "message": "🔍 Analyzing your question..."}

        # Step 1: PARALLEL query analysis + embedding (0ms added latency)
        # Run LLM analysis in parallel with embedding generation
        step_start = time.time()
        
        # Check query pattern cache first (instant if cached)
        cache_key = question.lower().strip()[:100]
        cached_analysis = self._query_pattern_cache.get(cache_key)
        
        if cached_analysis:
            query_analysis = cached_analysis
            print("[RAG STREAM] Step 1a - Query Analysis (CACHED): 0.00s")
        else:
            # Start LLM analysis as background task
            analysis_task = asyncio.create_task(self._analyze_query_with_llm(question))
        
        # Generate embedding with basic expansion (runs in parallel with LLM analysis)
        basic_expanded = self._expand_query(question)
        query_embedding = self.encode(basic_expanded)[0].tolist()
        
        # Wait for LLM analysis if not cached
        if not cached_analysis:
            query_analysis = await analysis_task
            # Cache the analysis for similar future queries
            self._query_pattern_cache[cache_key] = query_analysis
            # Limit cache size
            if len(self._query_pattern_cache) > 100:
                # Remove oldest entries
                keys = list(self._query_pattern_cache.keys())
                for k in keys[:20]:
                    del self._query_pattern_cache[k]
        
        print(f"[RAG STREAM] Step 1 - Parallel Analysis+Embedding: {time.time() - step_start:.2f}s")
        
        # Build optimized search query from analysis (fast, no I/O)
        expanded_query = self._build_search_query(query_analysis, question)
        expanded_query = self._expand_query(expanded_query)
        if expanded_query != question:
            print(f"[RAG STREAM] Query expanded: '{question[:30]}...' -> '{expanded_query[:50]}...'")

        # Step 2: Get table and check for data
        table = self._get_table(notebook_id)
        try:
            row_count = table.count_rows()
            print(f"[RAG STREAM] Table has {row_count} rows")
            if row_count == 0:
                await rag_metrics.end_query((time.time() - total_start) * 1000)
                yield {"type": "error", "content": "No documents indexed yet."}
                return
        except Exception as e:
            print(f"[RAG STREAM] Error counting rows: {e}")

        # Status 1: Searching
        yield {"type": "status", "message": "🔍 Searching your documents..."}
        
        # v1.1.0: Send retrieval_start event for progressive UI
        yield {
            "type": "retrieval_start",
            "query_analysis": {
                "entities": query_analysis.get("entities", []),
                "time_periods": query_analysis.get("time_periods", []),
                "data_type": query_analysis.get("data_type", "unknown"),
                "key_metric": query_analysis.get("key_metric", "")
            }
        }

        # Step 2b: Adaptive search with multiple strategies and verification
        step_start = time.time()
        overcollect_k = settings.retrieval_overcollect if self._use_reranker else top_k
        try:
            # Use adaptive search that tries multiple strategies
            results, strategies_used = await self._adaptive_search_progressive(
                table, question, query_embedding, query_analysis, overcollect_k
            )
            search_time = time.time() - step_start
            print(f"[RAG STREAM] Step 2 - Adaptive Search ({len(results)} results): {search_time:.2f}s")
            
            # v1.1.0: Send retrieval progress with preliminary results
            yield {
                "type": "retrieval_progress",
                "chunks_found": len(results),
                "strategies_tried": strategies_used,
                "search_time_ms": int(search_time * 1000)
            }
        except Exception as e:
            print(f"[RAG STREAM] Search exception: {e}")
            traceback.print_exc()
            rag_metrics.record_error(str(e), RAGStage.VECTOR_SEARCH)
            await rag_metrics.end_query((time.time() - total_start) * 1000)
            yield {"type": "error", "content": f"Search error: {e}"}
            return

        # Filter by source_ids if specified
        if source_ids:
            results = [r for r in results if r["source_id"] in source_ids]

        # P1: Start follow-up generation EARLY — uses raw search text, runs parallel with rerank+LLM
        early_context = "\n\n".join([r.get("text", "")[:300] for r in results[:5]])
        followup_task = asyncio.create_task(
            self._generate_follow_up_questions_fast(question, early_context)
        )

        # Step 2c: Rerank
        rerank_time = 0
        if self._use_reranker and len(results) > top_k:
            # Status 2: Reranking (only shown if reranker is used)
            yield {"type": "status", "message": "📊 Reranking by relevance..."}
            step_start = time.time()
            results = self.rerank(question, results, top_k=top_k + 1)
            rerank_time = time.time() - step_start
            print(f"[RAG STREAM] Step 2c - Reranking ({len(results)} results): {rerank_time:.2f}s")

        # Step 2d: Entity boosting (Phase 2.2) - use entities from analysis
        entities = query_analysis.get("entities", []) or self._extract_entities(question)
        if entities:
            print(f"[RAG STREAM] Entities detected: {entities}")
            results = self._boost_entity_matches(results, entities)

        # Step 2e: Temporal boosting (Phase 2.4)
        temporal_filter = self._extract_temporal_filter(question)
        if temporal_filter:
            print(f"[RAG STREAM] Temporal filter detected: {temporal_filter}")
            results = self._boost_temporal_relevance(results, temporal_filter)

        # Step 2f: Ensure source diversity (Phase 2.3)
        results = self._ensure_source_diversity(results)

        # Step 3: Build citations and context (shared helper)
        step_start = time.time()
        citations, sources, context, low_confidence = await self._build_citations_and_context(results, "[RAG STREAM]")
        num_citations = len(citations)
        print(f"[RAG STREAM] Step 3 - Build context: {time.time() - step_start:.2f}s ({len(context)} chars)")

        # Status 3: Found sections (with count and sources)
        if citations:
            source_names = list(set(c.get("filename", "document") for c in citations[:3]))
            sources_str = ", ".join(source_names[:2])
            if len(source_names) > 2:
                sources_str += f" +{len(source_names) - 2} more"
            yield {"type": "status", "message": f"📄 Found {len(citations)} relevant sections from {sources_str}"}

        # Send citations immediately so UI can show them
        yield {
            "type": "citations",
            "citations": citations,
            "sources": list(sources),
            "low_confidence": low_confidence
        }

        # Step 4: Handle low confidence case - refuse to answer if no valid sources
        if low_confidence or num_citations == 0 or not context.strip():
            no_info_msg = "I don't have enough relevant information in your documents to answer this question accurately. Try uploading more documents related to this topic, or rephrase your question."
            yield {"type": "token", "content": no_info_msg}
            followup_task.cancel()
            yield {"type": "done", "follow_up_questions": []}
            return

        # Step 5: Classify query and select optimal prompt + model
        from api.settings import get_user_profile_sync, build_user_context
        user_profile = get_user_profile_sync()
        user_context = build_user_context(user_profile)
        
        # Phase 0.2 + 1.3: Query classification determines prompt and model
        query_type = self._classify_query(question)
        if deep_think:
            query_type = 'complex'  # Override if user explicitly requested deep think
        
        # Phase 4.3: Calculate average confidence for verbalization
        avg_confidence = sum(c.get("confidence", 0.5) for c in citations) / max(len(citations), 1)
        
        # Get optimized prompt for this query type (with confidence guidance)
        base_prompt = self._get_prompt_for_query_type(query_type, num_citations, avg_confidence)
        format_hint = self._detect_response_format(question)
        system_prompt = f"User context: {user_context}\n\n{base_prompt}{format_hint}" if user_context else f"{base_prompt}{format_hint}"

        # Build user prompt with temporal context if detected
        temporal_note = ""
        if temporal_filter:
            periods = []
            for q in (temporal_filter.get('quarters') or []):
                periods.append(f"Q{q}")
            for y in (temporal_filter.get('years') or []):
                periods.append(y)
            for fy in (temporal_filter.get('fiscal_years') or []):
                periods.append(f"FY {fy}")
            if periods:
                temporal_note = f"\n\nIMPORTANT: This question is specifically about {', '.join(periods)}. Only use data from this exact time period."
        
        # v1.1.0: Add community context for holistic queries
        community_context = ""
        try:
            from services.community_detection import community_detector
            if community_detector.is_holistic_query(question):
                # Get entities from query analysis
                query_entities = query_analysis.get("entities", [])
                if query_entities:
                    community_context = community_detector.get_community_context(
                        notebook_id, query_entities
                    )
                    if community_context:
                        print("[RAG STREAM] Added community context for holistic query")
        except Exception as e:
            print(f"[RAG STREAM] Community context failed (non-fatal): {e}")
        
        prompt = f"""{community_context}Sources:
{context}

Question: {question}{temporal_note}

Answer with [N] citations:"""

        # Two-tier model routing:
        # - System 1 (phi4-mini): Factual queries - fast, reliable
        # - System 2 (olmo-3:7b-instruct): Synthesis/complex queries - thorough, good reasoning
        use_fast_model = (query_type == 'factual') and not deep_think
        
        model_choice = "phi4-mini (fast)" if use_fast_model else "olmo-3:7b-instruct (main)"
        print(f"[RAG STREAM] Query type: {query_type}, using {model_choice}")
        
        # Status 4: Generating answer
        thinking_msg = "🧠 Deep thinking..." if deep_think else ("🤔 Synthesizing answer..." if query_type == 'complex' else "✍️ Generating answer...")
        yield {
            "type": "status",
            "message": thinking_msg,
            "query_type": query_type
        }

        # ── Stream-First, Verify-After ──────────────────────────────────────
        # Tokens stream to the frontend in real-time for immediate feedback.
        # After generation completes, CaRR verification runs silently.
        # If high hallucination risk is detected and a better answer is produced,
        # a replace_answer event swaps the content (frontend already handles this).
        step_start = time.time()
        full_answer = ""
        references_started = False
        
        # Phase 1: STREAM — emit tokens in real-time as they arrive
        async for token in self._stream_ollama(system_prompt, prompt, deep_think=deep_think, use_fast_model=use_fast_model):
            full_answer += token
            
            # Detect references section — stop streaming there
            if not references_started:
                lower_buf = full_answer.lower()
                for marker in ["\nreferences:", "\nreferences\n", "\n**references", "\nsources:\n", "\n**sources", "\ncitations:\n", "\n\n[1] "]:
                    if marker in lower_buf:
                        references_started = True
                        idx = lower_buf.find(marker)
                        full_answer = full_answer[:idx]
                        print("[RAG STREAM] Detected references section, truncating")
                        break
            
            # Stream token to frontend immediately (skip if in references)
            if not references_started:
                yield {"type": "token", "content": token}
        
        gen_time = time.time() - step_start
        # Post-generation cleanup: strip trailing reference stubs the marker
        # detection missed (e.g. "---\n\nN." or trailing "---")
        import re as _re
        full_answer = _re.sub(r'\n\n---+\s*\n[\s\S]{0,40}$', '', full_answer)
        full_answer = _re.sub(r'\n\n---+\s*$', '', full_answer)
        full_answer = _re.sub(r'\n\n[A-Z0-9]\.\s*$', '', full_answer)
        print(f"[RAG STREAM] Step 6a - LLM generation (streamed): {gen_time:.2f}s ({len(full_answer)} chars)")

        # Phase 2: VERIFY — silent citation check after streaming completes
        step_start = time.time()
        verification_result = rag_verification.verify_answer(full_answer, citations)
        carr_retried = False
        
        if verification_result and verification_result.hallucination_risk == "high":
            print(f"[RAG STREAM] CaRR: high hallucination risk detected, running retry...")
            yield {"type": "status", "message": "🔄 Improving answer accuracy..."}
            should_replace, new_answer, updated_verif = await rag_verification.attempt_carr_retry(
                verification_result=verification_result,
                system_prompt=system_prompt,
                user_prompt=prompt,
                citations=citations,
                deep_think=deep_think,
                use_fast_model=use_fast_model,
                original_answer=full_answer,
            )
            if should_replace and new_answer:
                full_answer = new_answer
                carr_retried = True
                if updated_verif:
                    verification_result = updated_verif
                # Replace the already-streamed answer with the improved version
                yield {"type": "replace_answer", "content": new_answer}
                print(f"[RAG STREAM] CaRR: retry accepted, replaced streamed answer")
            else:
                print(f"[RAG STREAM] CaRR: retry rejected, keeping streamed answer")
        
        verify_time = time.time() - step_start
        print(f"[RAG STREAM] Step 6b - Verification{' + CaRR retry' if carr_retried else ''}: {verify_time:.2f}s")

        # Step 7: Collect follow-ups (started early at Step 2b, should be ready by now)
        step_start = time.time()
        follow_up_questions = []
        if followup_task.done():
            follow_up_questions = followup_task.result()
            print(f"[RAG STREAM] Step 7 - Follow-ups ready (instant): {time.time() - step_start:.2f}s")
        else:
            try:
                follow_up_questions = await asyncio.wait_for(followup_task, timeout=2.0)
                print(f"[RAG STREAM] Step 7 - Follow-ups ready (waited): {time.time() - step_start:.2f}s")
            except asyncio.TimeoutError:
                print(f"[RAG STREAM] Step 7 - Follow-ups timed out, sending done without them")

        if follow_up_questions:
            yield {"type": "follow_up_questions", "questions": follow_up_questions}

        # Build verification payload for done event
        verification_payload = rag_verification.build_verification_payload(verification_result, carr_retried)
        
        yield {
            "type": "done",
            "follow_up_questions": follow_up_questions,
            "verification": verification_payload
        }

        # Late follow-ups (if not ready at done time)
        if not follow_up_questions and not followup_task.cancelled():
            try:
                follow_up_questions = await asyncio.wait_for(followup_task, timeout=5.0)
                if follow_up_questions:
                    yield {"type": "follow_up_questions", "questions": follow_up_questions}
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Step 8: Memory extraction (fire-and-forget)
        async def _extract_memories_background():
            try:
                conversation_id = str(uuid.uuid4())
                await memory_agent.extract_memories(MemoryExtractionRequest(
                    message=question, role="user",
                    conversation_id=conversation_id, notebook_id=notebook_id
                ))
                await memory_agent.extract_memories(MemoryExtractionRequest(
                    message=full_answer, role="assistant",
                    conversation_id=conversation_id, notebook_id=notebook_id, context=question
                ))
            except Exception as e:
                print(f"[RAG STREAM] Memory extraction failed (non-fatal): {e}")
        
        asyncio.create_task(_extract_memories_background())
        
        # v1.0.7: FAST pre-classify content INLINE for guaranteed instant visual generation
        # Uses regex extraction (~2ms) instead of slow LLM analysis
        # This runs INLINE (not background) so cache is ready before user can click "Create Visual"
        try:
            from services.visual_analyzer import visual_analyzer
            await visual_analyzer.pre_classify_fast(
                notebook_id=notebook_id,
                query=question,
                answer=full_answer
            )
        except Exception as e:
            print(f"[RAG STREAM] Fast visual pre-classification failed (non-fatal): {e}")

        # v1.0.6: Finalize metrics for streaming query
        total_time_ms = (time.time() - total_start) * 1000
        await rag_metrics.end_query(total_time_ms)
        
        print(f"{'='*60}")
        print(f"[RAG STREAM] TOTAL time: {total_time_ms/1000:.2f}s")
        print(f"{'='*60}\n")

    def _clean_llm_output(self, text: str) -> str:
        """Clean up LLM output artifacts."""
        return rag_generation.clean_llm_output(text)

    async def generate_proactive_insights(self, notebook_id: str, limit: int = 3) -> List[Dict]:
        """Generate proactive insights from document content."""
        return await rag_generation.generate_proactive_insights(notebook_id, limit)

    async def _generate_follow_up_questions_fast(self, question: str, context: str, answer: str = "") -> List[str]:
        """Generate contextual follow-up questions using fast model."""
        return await rag_generation.generate_follow_up_questions_fast(question, context, answer)

    async def get_suggested_questions(self, notebook_id: str) -> List[str]:
        """Generate suggested questions based on actual document content."""
        return await rag_generation.get_suggested_questions(notebook_id)
    
    def _default_suggested_questions(self) -> List[str]:
        """Fallback suggested questions"""
        return rag_generation.default_suggested_questions()

    async def _generate_answer(self, question: str, context: str, num_citations: int = 5, llm_provider: Optional[str] = None, notebook_id: Optional[str] = None, conversation_id: Optional[str] = None, deep_think: bool = False) -> Dict:
        """Generate answer using LLM with memory augmentation and user personalization."""
        return await rag_generation.generate_answer(
            question, context, num_citations=num_citations, llm_provider=llm_provider,
            notebook_id=notebook_id, conversation_id=conversation_id, deep_think=deep_think,
            detect_response_format_fn=self._detect_response_format
        )
    
    async def _call_ollama(self, system_prompt: str, prompt: str, model: str = None, num_predict: int = 500, num_ctx: int = None, temperature: float = None, repeat_penalty: float = None, extra_options: dict = None) -> str:
        """Call Ollama API
        
        Args:
            num_predict: Max tokens to generate. 500 for chat Q&A, 2000-4000 for documents.
            num_ctx: Context window size. None = Ollama default. Set higher (8192+) for long generation.
            temperature: LLM temperature. None = Ollama default (~0.7).
            repeat_penalty: Repetition penalty. None = auto. Use 1.1 for dialogue scripts.
            extra_options: Additional Ollama options merged last (e.g., Mirostat overrides).
        """
        return await rag_llm.call_ollama(system_prompt, prompt, model=model, num_predict=num_predict, num_ctx=num_ctx, temperature=temperature, repeat_penalty=repeat_penalty, extra_options=extra_options)

    async def _stream_ollama(self, system_prompt: str, prompt: str, deep_think: bool = False, use_fast_model: bool = False, num_predict: Optional[int] = None, temperature_override: Optional[float] = None, extra_options: dict = None) -> AsyncGenerator[str, None]:
        """Stream response from Ollama API with stop sequences to prevent citation lists
        
        Args:
            deep_think: Use CoT prompting with lower temperature for thorough analysis
            use_fast_model: Use phi4-mini (System 1) instead of olmo-3:7b-instruct (System 2)
            num_predict: Override token limit. None = use defaults (800 chat / 1500 deep think).
                         Set higher (2000-4000) for document generation.
            temperature_override: Per-skill adaptive temperature. None = use model defaults.
            extra_options: Additional Ollama options merged last (e.g., Mirostat overrides).
        """
        async for token in rag_llm.stream_ollama(system_prompt, prompt, deep_think=deep_think, use_fast_model=use_fast_model, num_predict=num_predict, temperature_override=temperature_override, extra_options=extra_options):
            yield token

    async def _call_openai(self, system_prompt: str, prompt: str) -> str:
        """Call OpenAI API"""
        return await rag_llm.call_openai(system_prompt, prompt)

    async def _call_anthropic(self, system_prompt: str, prompt: str) -> str:
        """Call Anthropic API"""
        return await rag_llm.call_anthropic(system_prompt, prompt)

    def _chunk_text_smart(self, text: str, source_type: str, filename: str) -> List[str]:
        """Smart chunking that adapts strategy based on source type."""
        return rag_chunking.chunk_text_smart(text, source_type, filename)
    
    def _chunk_hierarchical(self, text: str, filename: str) -> List[str]:
        """Hierarchical chunking for structured documents."""
        return rag_chunking.chunk_hierarchical(text, filename)
    
    def _chunk_tabular_data(self, text: str) -> List[str]:
        """Chunk tabular data keeping related rows together with context."""
        return rag_chunking.chunk_tabular_data(text)

    def _chunk_text(self, text: str) -> List[str]:
        """Chunk text with semantic boundary awareness."""
        return rag_chunking.chunk_text(text)
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        return rag_chunking.split_into_sentences(text)
    
    def _char_split(self, text: str, chunk_size: int, overlap: int) -> List[str]:
        """Fallback character-based splitting."""
        return rag_chunking.char_split(text, chunk_size, overlap)

    def _get_parent_context(self, chunks: List[str], chunk_index: int, max_parent_chars: int = 2000) -> str:
        """Get expanded parent context for a chunk."""
        return rag_chunking.get_parent_context(chunks, chunk_index, max_parent_chars)

    def get_current_embedding_dim(self) -> int:
        """Get the dimension of the current embedding model"""
        return rag_embeddings.get_current_embedding_dim()

    def check_embedding_dimension_mismatch(self) -> List[str]:
        """Check all notebook tables for embedding dimension mismatch.
        Returns list of notebook IDs that need re-indexing."""
        return rag_storage.check_embedding_dimension_mismatch()


# Global instance
rag_engine = RAGEngine()
