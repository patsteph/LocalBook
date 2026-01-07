"""RAG (Retrieval Augmented Generation) Engine

Implements hybrid search (BM25 + Vector) for maximum retrieval accuracy.
Vector search captures semantic similarity, BM25 captures exact keyword matches.
"""
import asyncio
import json
import os
import re
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Optional, AsyncGenerator, Tuple, Union

import httpx
import lancedb
import numpy as np

from config import settings
# v0.6.5: ConceptExtractionRequest no longer needed - using BERTopic
from models.memory import MemoryExtractionRequest
from services.memory_agent import memory_agent
from storage.source_store import source_store

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
        # Prefer FlashRank (ultra-fast, no torch)
        if HAS_FLASHRANK and settings.reranker_type == "flashrank":
            if self.flashrank_reranker is None:
                self.flashrank_reranker = FlashRanker(
                    model_name=settings.reranker_model,
                    max_length=256  # Optimized for typical chunk sizes
                )
                print(f"[RAG] Loaded FlashRank reranker: {settings.reranker_model}")
            return self.flashrank_reranker
        
        # Fallback to cross-encoder (slower but works without FlashRank)
        if self.reranker is None:
            from sentence_transformers import CrossEncoder
            reranker_model = "BAAI/bge-reranker-v2-m3"  # Cross-encoder fallback
            self.reranker = CrossEncoder(reranker_model, max_length=512)
            print(f"[RAG] Loaded cross-encoder reranker: {reranker_model}")
        return self.reranker
    
    def _load_reranker(self):
        """Force load the reranker model (used for warmup)"""
        if self._use_reranker:
            return self._get_reranker()
        return None
    
    def _hybrid_search(
        self, 
        query: str, 
        table, 
        query_embedding: List[float], 
        k: int = 12
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
            vector_results = table.search(query_embedding).limit(k*2).to_list()
        except:
            vector_results = all_docs[:k*2]
        
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
            vector_ranking = {doc["source_id"] + str(doc.get("chunk_index", 0)): i 
                           for i, doc in enumerate(vector_results)}
            
            bm25_ranked_indices = np.argsort(bm25_scores)[::-1][:k*2]
            bm25_ranking = {all_docs[idx]["source_id"] + str(all_docs[idx].get("chunk_index", 0)): i 
                          for i, idx in enumerate(bm25_ranked_indices)}
            
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
            
            # Map back to documents
            doc_map = {doc["source_id"] + str(doc.get("chunk_index", 0)): doc for doc in all_docs}
            hybrid_results = [doc_map[key] for key in sorted_keys[:k] if key in doc_map]
            
            print(f"[RAG] Hybrid search: {len(hybrid_results)} results (vector + BM25 fusion)")
            return hybrid_results
            
        except Exception as e:
            print(f"[RAG] BM25 failed, using vector-only: {e}")
            return vector_results[:k]
    
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """Rerank documents using FlashRank (preferred) or cross-encoder for better relevance"""
        if not documents:
            return documents
        
        reranker = self._get_reranker()
        
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
    
    def _load_embedding_model(self):
        """Force load the embedding model (used for warmup)"""
        if self._use_ollama_embeddings:
            # For Ollama, we just need to make sure the model is pulled
            # Warmup is handled by model_warmup.py
            return None
        if self.embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self.embedding_model = SentenceTransformer(settings.embedding_model)
        return self.embedding_model
    
    def _get_embedding_model(self):
        """Lazy load embedding model (for sentence-transformers fallback)"""
        if self._use_ollama_embeddings:
            return None
        if self.embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self.embedding_model = SentenceTransformer(settings.embedding_model)
        return self.embedding_model
    
    def _get_ollama_embedding_sync(self, text: str) -> List[float]:
        """Get embedding from Ollama synchronously"""
        import requests
        response = requests.post(
            f"{settings.ollama_base_url}/api/embeddings",
            json={
                "model": settings.embedding_model,
                "prompt": text
            },
            timeout=60
        )
        result = response.json()
        return result.get("embedding", [])
    
    def _get_ollama_embeddings_batch_sync(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for multiple texts from Ollama.
        
        Uses sequential processing to ensure reliability. Parallel processing
        was causing issues with empty embeddings on failures.
        """
        embeddings = []
        for i, text in enumerate(texts):
            try:
                embedding = self._get_ollama_embedding_sync(text)
                if not embedding or len(embedding) == 0:
                    # Retry once on empty embedding
                    print(f"[RAG] Empty embedding for chunk {i}, retrying...")
                    embedding = self._get_ollama_embedding_sync(text)
                embeddings.append(embedding)
            except Exception as e:
                print(f"[RAG] Embedding failed for chunk {i}: {e}")
                # On failure, use a zero vector of expected dimension
                embeddings.append([0.0] * settings.embedding_dim)
        
        return embeddings
    
    async def _get_ollama_embedding(self, text: str) -> List[float]:
        """Get embedding from Ollama asynchronously"""
        timeout = httpx.Timeout(60.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={
                    "model": settings.embedding_model,
                    "prompt": text
                }
            )
            result = response.json()
            return result.get("embedding", [])
    
    def encode(self, texts: Union[str, List[str]]) -> np.ndarray:
        """Encode texts to embeddings (compatible with SentenceTransformer interface)"""
        if isinstance(texts, str):
            texts = [texts]
        
        if self._use_ollama_embeddings:
            embeddings = self._get_ollama_embeddings_batch_sync(texts)
            return np.array(embeddings)
        else:
            model = self._get_embedding_model()
            return model.encode(texts)

    def _get_stored_vector_dim(self, table) -> Optional[int]:
        """Get the dimension of vectors stored in a table from schema"""
        try:
            schema = table.schema
            for field in schema:
                if field.name == "vector":
                    type_str = str(field.type)
                    if "fixed_size_list" in type_str:
                        match = re.search(r'\[(\d+)\]', type_str)
                        if match:
                            return int(match.group(1))
        except Exception:
            pass
        return None

    def _get_table(self, notebook_id: str):
        """Get or create LanceDB table for notebook"""
        if self.db is None:
            self.db = lancedb.connect(str(self.db_path))

        table_name = f"notebook_{notebook_id}"

        # Check if table exists
        if table_name not in self.db.table_names():
            # Create table with schema including useful metadata fields
            # v0.60: Include parent_text for expanded context retrieval
            placeholder_embedding = self.encode("placeholder")[0].tolist()
            self.db.create_table(
                table_name,
                data=[{
                    "vector": placeholder_embedding,
                    "text": "placeholder",
                    "parent_text": "",  # v0.60: Parent document context
                    "source_id": "placeholder",
                    "chunk_index": 0,
                    "filename": "placeholder",
                    "source_type": "placeholder"
                }]
            )
            # Delete placeholder
            table = self.db.open_table(table_name)
            table.delete("source_id = 'placeholder'")
        else:
            table = self.db.open_table(table_name)

        return table
    
    def _table_has_parent_text(self, table) -> bool:
        """Check if table schema includes parent_text column"""
        try:
            schema = table.schema
            for field in schema:
                if field.name == "parent_text":
                    return True
        except Exception:
            pass
        return False

    async def _build_citations_and_context(
        self, 
        results: List[Dict], 
        log_prefix: str = "[RAG]"
    ) -> Tuple[List[Dict], set, str, bool]:
        """Build citations and context from search results.
        
        Returns: (citations, sources_set, context_string, low_confidence)
        """
        # Get source filenames for citations
        source_filenames = {}
        for result in results:
            sid = result["source_id"]
            if sid not in source_filenames:
                source_data = await source_store.get(sid)
                source_filenames[sid] = source_data.get("filename", "Unknown") if source_data else "Unknown"

        # Build citations from search results
        all_citations = []
        for i, result in enumerate(results):
            text = result.get("text", "")
            
            # Use rerank_score if available (from cross-encoder), otherwise use vector distance
            if "rerank_score" in result:
                # Reranker scores are typically -10 to +10, normalize to 0-1
                # Scores > 0 are relevant, < 0 are irrelevant
                rerank_score = result.get("rerank_score", 0)
                confidence = max(0, min(1, (rerank_score + 5) / 10))  # -5 -> 0%, +5 -> 100%
                print(f"{log_prefix} Citation {i+1}: rerank_score={rerank_score:.2f} -> confidence={confidence:.0%}")
            else:
                # LanceDB uses L2 (Euclidean) distance by default
                # Typical ranges: 0-50 = very similar, 50-150 = somewhat similar, 150+ = less similar
                # Good matches are typically < 100 distance
                distance = result.get("_distance", 100.0)
                # Convert to confidence using empirically-tuned thresholds
                # 0 dist = 100%, 50 dist = 75%, 100 dist = 50%, 200 dist = 25%, 400+ dist = 0%
                confidence = max(0, min(1, 1 - (distance / 400)))
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

    async def ingest_document(
        self,
        notebook_id: str,
        source_id: str,
        text: str,
        filename: str = "Unknown",
        source_type: str = "document"
    ) -> Dict:
        """Ingest a document into the RAG system"""

        # Use source-type-aware chunking for better retrieval
        chunks = self._chunk_text_smart(text, source_type, filename)

        # Skip summary for web sources (they have search snippets already)
        # Only generate summaries for uploaded files (PDFs, docs, etc.)
        summary = None
        if source_type not in ['web', 'youtube']:
            summary = await self._generate_document_summary(text, filename, source_type)
            if summary:
                print(f"[RAG] Generated summary for {filename}: {len(summary)} chars")

        # Generate embeddings
        embeddings = self.encode(chunks)

        # Insert into LanceDB
        table = self._get_table(notebook_id)
        
        # Check if table supports parent_text (v0.60 feature)
        # Older tables may not have this column
        has_parent_text = self._table_has_parent_text(table)
        
        # Prepare data for insertion with metadata
        data = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            row = {
                "vector": embedding.tolist(),
                "text": chunk,
                "source_id": source_id,
                "chunk_index": i,
                "filename": filename,
                "source_type": source_type
            }
            # Only include parent_text if table supports it
            if has_parent_text:
                row["parent_text"] = self._get_parent_context(chunks, i, max_parent_chars=2000)
            data.append(row)
        
        # Add summary as a special chunk (chunk_index = -1) for quick retrieval
        if summary:
            summary_embedding = self.encode(summary)[0].tolist()
            summary_row = {
                "vector": summary_embedding,
                "text": f"[SUMMARY] {summary}",
                "source_id": source_id,
                "chunk_index": -1,  # Special index for summaries
                "filename": filename,
                "source_type": "summary"
            }
            if has_parent_text:
                summary_row["parent_text"] = ""
            data.append(summary_row)

        table.add(data)

        # Fire-and-forget topic modeling in background
        # Source is usable for RAG queries immediately after embedding
        # Topics build in background for Knowledge Graph visualization
        # v0.6.5: Use BERTopic with ALL chunks (no more 2000 char limit)
        asyncio.create_task(self._add_to_topic_model(
            notebook_id=notebook_id,
            source_id=source_id,
            chunks=chunks,
            embeddings=embeddings
        ))
        print(f"[RAG] Queued topic modeling for {filename} (background)")

        return {
            "source_id": source_id,
            "chunks": len(chunks),
            "characters": len(text),
            "summary": summary
        }

    async def delete_source(self, notebook_id: str, source_id: str) -> bool:
        """Delete all chunks for a source from LanceDB.
        
        This should be called when a source is deleted to clean up vector embeddings.
        """
        try:
            table = self._get_table(notebook_id)
            # LanceDB delete uses SQL-like filter syntax
            table.delete(f"source_id = '{source_id}'")
            print(f"[RAG] Deleted all chunks for source {source_id} from LanceDB")
            return True
        except Exception as e:
            print(f"[RAG] Error deleting source {source_id} from LanceDB: {e}")
            return False

    async def _generate_document_summary(self, text: str, filename: str, source_type: str) -> Optional[str]:
        """Phase 3.1: Generate a summary of the document at ingestion time.
        
        This summary is stored as a special chunk and helps with:
        - Quick overview queries
        - Better context for factual questions
        - Faster retrieval for general questions about the document
        """
        # For very short documents, don't generate summary
        if len(text) < 500:
            return None
        
        # Truncate for summary generation (first ~4000 chars is usually enough)
        text_sample = text[:4000]
        
        # Different prompts for different source types
        if source_type in ['xlsx', 'csv', 'tabular']:
            prompt = f"""Summarize this tabular data from '{filename}'. Include:
- What entities/people are tracked
- What metrics/values are recorded  
- Time periods covered
- Key totals or patterns

Data sample:
{text_sample}

Summary (2-3 sentences):"""
        else:
            prompt = f"""Summarize the key points from '{filename}' in 2-3 sentences. Focus on:
- Main topic/purpose
- Key facts or findings
- Important entities mentioned

Content:
{text_sample}

Summary:"""
        
        try:
            # Use fast model for summary generation (it's good at summarization)
            timeout = httpx.Timeout(30.0, read=60.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "num_predict": 200,
                        }
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    summary = result.get("response", "").strip()
                    # Clean up any artifacts
                    if summary and len(summary) > 20:
                        return summary
        except Exception as e:
            print(f"[RAG] Summary generation failed for {filename}: {e}")
        
        return None

    async def _add_to_topic_model(
        self,
        notebook_id: str,
        source_id: str,
        chunks: List[str],
        embeddings: np.ndarray
    ):
        """Add document chunks to BERTopic model for topic discovery.
        
        v0.6.5: Replaces concept extraction with BERTopic topic modeling.
        Uses ALL chunks (not limited to 2000 chars) for better topic discovery.
        Two-stage naming: instant c-TF-IDF + background LLM enhancement.
        """
        async with _concept_extraction_semaphore:
            try:
                from services.topic_modeling import topic_modeling_service
                
                if not chunks:
                    print(f"[TopicModel] No chunks for source {source_id}, skipping")
                    return
                
                print(f"[TopicModel] Adding {len(chunks)} chunks from source {source_id}")
                
                result = await topic_modeling_service.add_documents(
                    texts=chunks,
                    source_id=source_id,
                    notebook_id=notebook_id,
                    embeddings=embeddings
                )
                
                topic_count = len(result.get("topics", []))
                if topic_count > 0:
                    print(f"[TopicModel] Found {topic_count} topics for source {source_id}")
                else:
                    print(f"[TopicModel] No new topics for source {source_id} (status: {result.get('status', 'unknown')})")
                
            except Exception as e:
                import traceback
                print(f"[TopicModel] Error adding to topic model: {e}")
                traceback.print_exc()

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
        print(f"\n{'='*60}")
        print(f"[RAG] Starting query: '{question[:50]}...'")
        print(f"{'='*60}")

        # Step 1: PARALLEL query analysis + embedding (0ms added latency)
        step_start = time.time()
        
        # Check query pattern cache first
        cache_key = question.lower().strip()[:100]
        cached_analysis = self._query_pattern_cache.get(cache_key)
        
        if cached_analysis:
            query_analysis = cached_analysis
        else:
            analysis_task = asyncio.create_task(self._analyze_query_with_llm(question))
        
        # Generate embedding in parallel
        basic_expanded = self._expand_query(question)
        query_embedding = self.encode(basic_expanded)[0].tolist()
        
        if not cached_analysis:
            query_analysis = await analysis_task
            self._query_pattern_cache[cache_key] = query_analysis
            if len(self._query_pattern_cache) > 100:
                keys = list(self._query_pattern_cache.keys())
                for k in keys[:20]:
                    del self._query_pattern_cache[k]
        
        print(f"[RAG] Step 1 - Parallel Analysis+Embedding: {time.time() - step_start:.2f}s")
        
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

        # Step 3: Adaptive search with multiple strategies
        step_start = time.time()
        overcollect_k = settings.retrieval_overcollect if self._use_reranker else top_k
        try:
            results = await self._adaptive_search(
                table, question, query_embedding, query_analysis, overcollect_k
            )
            print(f"[RAG] Step 2 - Adaptive Search ({len(results)} results): {time.time() - step_start:.2f}s")
        except Exception as e:
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
            step_start = time.time()
            results = self.rerank(question, results, top_k=top_k + 1)
            print(f"[RAG] Step 3 - Reranking ({len(results)} results): {time.time() - step_start:.2f}s")

        # Step 4: Build citations and context (shared helper)
        step_start = time.time()
        citations, sources, context, low_confidence = await self._build_citations_and_context(results, "[RAG]")
        num_citations = len(citations)
        print(f"[RAG] Step 4 - Build context: {time.time() - step_start:.2f}s")

        # Step 5: Generate answer
        step_start = time.time()
        conversation_id = str(uuid.uuid4())
        query_type = self._classify_query(question)
        answer_result = await self._generate_answer(question, context, num_citations, llm_provider, notebook_id, conversation_id)
        answer = answer_result["answer"]
        memory_used = answer_result.get("memory_used", [])
        memory_context_summary = answer_result.get("memory_context_summary")
        print(f"[RAG] Step 5 - LLM answer: {time.time() - step_start:.2f}s")
        
        # Step 5b: Quality check + corrective retrieval if needed
        quality_ok, quality_reason = self._check_answer_quality(question, answer, query_type)
        if not quality_ok:
            print(f"[RAG] Quality check failed: {quality_reason}")
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
        else:
            print(f"[RAG] Quality check passed")

        # Step 6: Generate follow-up questions
        step_start = time.time()
        follow_up_questions = await self._generate_follow_up_questions_fast(question, context)
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

        total_time = time.time() - total_start
        print(f"{'='*60}")
        print(f"[RAG] TOTAL query time: {total_time:.2f}s")
        print(f"{'='*60}\n")

        return {
            "answer": answer,
            "citations": citations,
            "sources": list(sources),
            "web_sources": None,
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
                    print(f"[RAG] LLM Query Analysis: {analysis}")
                    return analysis
        except Exception as e:
            print(f"[RAG] LLM query analysis failed: {e}, using fallback")
        
        # Fallback to basic extraction
        return self._fallback_query_analysis(question)
    
    def _fallback_query_analysis(self, question: str) -> Dict:
        """Fallback query analysis when LLM is unavailable."""
        q_lower = question.lower()
        
        # Extract time periods
        time_periods = []
        import re
        quarter_match = re.search(r'q([1-4])\s*(?:fy)?\s*(\d{4})?', q_lower)
        if quarter_match:
            q_num = quarter_match.group(1)
            year = quarter_match.group(2) or "2026"
            time_periods.append(f"Q {q_num} FY {year}")
        
        # Extract entities (capitalized words)
        entities = re.findall(r'\b([A-Z][a-z]+)\b', question)
        
        # Build search terms
        search_terms = list(set(question.lower().split()))
        
        return {
            "search_terms": search_terms,
            "entities": entities,
            "time_periods": time_periods,
            "data_type": "count" if any(w in q_lower for w in ["how many", "count", "number"]) else "explanation",
            "key_metric": None
        }
    
    def _build_search_query(self, analysis: Dict, original_question: str) -> str:
        """Build an optimized search query from LLM analysis."""
        parts = [original_question]
        
        # Add search term variations
        for term in analysis.get("search_terms", []):
            if term.lower() not in original_question.lower():
                parts.append(term)
        
        # Add entity variations
        for entity in analysis.get("entities", []):
            if entity.lower() not in original_question.lower():
                parts.append(entity)
        
        # Add time period variations
        for period in analysis.get("time_periods", []):
            parts.append(period)
        
        # Add metric-related terms
        if analysis.get("key_metric"):
            metric = analysis["key_metric"].lower()
            parts.append(metric)
            parts.append("record count")  # Common in tabular data
        
        return " ".join(parts)
    
    def _expand_query(self, question: str) -> str:
        """Phase 2.1: Query expansion with synonyms and related terms.
        
        Expands the query to improve retrieval by adding synonyms and
        common variations. This helps find relevant content even when
        the user uses different words than the document.
        """
        # Common business/sales synonyms
        expansions = {
            'demo': 'demo demonstration "record count"',
            'demos': 'demos demonstrations "record count"',
            'trial': 'trial pilot',
            'trials': 'trials pilots',
            'q1': 'q1 "q 1" "quarter 1" "first quarter" "Q 1 FY"',
            'q2': 'q2 "q 2" "quarter 2" "second quarter" "Q 2 FY"',
            'q3': 'q3 "q 3" "quarter 3" "third quarter" "Q 3 FY"',
            'q4': 'q4 "q 4" "quarter 4" "fourth quarter" "Q 4 FY"',
            'fy': 'fy "fiscal year"',
            'fy2026': 'fy2026 "fy 2026" "FY 2026"',
            'fy2025': 'fy2025 "fy 2025" "FY 2025"',
            'revenue': 'revenue sales income',
            'customer': 'customer client account',
            'customers': 'customers clients accounts',
            'meeting': 'meeting call conversation',
            'meetings': 'meetings calls conversations',
        }
        
        # Common name nicknames -> full names
        name_expansions = {
            'chris': 'chris christopher',
            'mike': 'mike michael',
            'dan': 'dan daniel',
            'bill': 'bill william',
            'bob': 'bob robert',
            'jim': 'jim james',
            'tom': 'tom thomas',
            'steve': 'steve stephen steven',
            'pat': 'pat patrick patricia',
            'jen': 'jen jennifer',
            'liz': 'liz elizabeth',
            'alex': 'alex alexander alexandra',
            'matt': 'matt matthew',
            'nick': 'nick nicholas',
            'sam': 'sam samuel samantha',
            'joe': 'joe joseph',
            'will': 'will william',
        }
        
        expanded = question
        q_lower = question.lower()
        
        for term, expansion in expansions.items():
            if term in q_lower and expansion not in q_lower:
                expanded = f"{expanded} {expansion}"
        
        # Expand nicknames to full names
        for nick, full in name_expansions.items():
            if nick in q_lower.split():  # Match whole word only
                expanded = f"{expanded} {full}"
        
        return expanded
    
    def _extract_entities(self, question: str) -> List[str]:
        """Phase 2.2: Extract named entities from query for better matching.
        
        Lightweight entity extraction without spaCy dependency.
        Focuses on names, proper nouns, and domain-specific terms.
        """
        import re
        
        entities = []
        
        # Extract capitalized words/phrases (likely names or proper nouns)
        # Match sequences of capitalized words
        cap_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
        cap_matches = re.findall(cap_pattern, question)
        entities.extend(cap_matches)
        
        # Common name patterns (first name + last name)
        name_pattern = r'\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b'
        name_matches = re.findall(name_pattern, question)
        for first, last in name_matches:
            entities.append(f"{first} {last}")
        
        # Extract quoted phrases (user explicitly marking important terms)
        quoted = re.findall(r'"([^"]+)"', question)
        entities.extend(quoted)
        quoted_single = re.findall(r"'([^']+)'", question)
        entities.extend(quoted_single)
        
        # Deduplicate while preserving order
        seen = set()
        unique_entities = []
        for e in entities:
            e_lower = e.lower()
            if e_lower not in seen and len(e) > 1:
                seen.add(e_lower)
                unique_entities.append(e)
        
        return unique_entities
    
    def _boost_entity_matches(self, results: List[Dict], entities: List[str]) -> List[Dict]:
        """Phase 2.2: Boost results containing extracted entities.
        
        Prioritizes chunks that mention the same entities as the query.
        """
        if not entities:
            return results
        
        def entity_score(result):
            text = result.get('text', '').lower()
            score = 0
            for entity in entities:
                if entity.lower() in text:
                    score += 2  # Higher weight for entity matches
            return score
        
        # Sort by entity score (descending), then by original order
        scored = [(entity_score(r), i, r) for i, r in enumerate(results)]
        scored.sort(key=lambda x: (-x[0], x[1]))
        
        boosted = [r for _, _, r in scored]
        
        # Log if we boosted anything
        top_scores = [s for s, _, _ in scored[:5]]
        if any(s > 0 for s in top_scores):
            print(f"[RAG] Entity boost applied for {entities}: top scores = {top_scores}")
        
        return boosted
    
    def _extract_temporal_filter(self, question: str) -> Optional[Dict]:
        """Phase 2.4: Extract temporal references from query for filtering.
        
        Detects time periods mentioned in the query (Q1 2026, FY 2025, etc.)
        to help prioritize temporally relevant chunks.
        """
        import re
        q_lower = question.lower()
        
        temporal_info = {
            'quarters': [],
            'years': [],
            'fiscal_years': []
        }
        
        # Extract quarters (Q1, Q2, Q3, Q4)
        quarter_patterns = [
            r'\bq\s*([1-4])\b',
            r'\bquarter\s*([1-4])\b',
            r'\b(first|second|third|fourth)\s+quarter\b'
        ]
        quarter_map = {'first': '1', 'second': '2', 'third': '3', 'fourth': '4'}
        
        for pattern in quarter_patterns:
            matches = re.findall(pattern, q_lower)
            for match in matches:
                if match in quarter_map:
                    temporal_info['quarters'].append(quarter_map[match])
                elif match.isdigit():
                    temporal_info['quarters'].append(match)
        
        # Extract years (2024, 2025, 2026, etc.)
        year_matches = re.findall(r'\b(20[2-3][0-9])\b', question)
        temporal_info['years'] = list(set(year_matches))
        
        # Extract fiscal years (FY 2025, FY2026, etc.)
        fy_matches = re.findall(r'\bfy\s*(\d{4}|\d{2})\b', q_lower)
        for fy in fy_matches:
            if len(fy) == 2:
                fy = '20' + fy
            temporal_info['fiscal_years'].append(fy)
        
        # Return None if no temporal info found
        if not any(temporal_info.values()):
            return None
        
        return temporal_info
    
    def _boost_temporal_relevance(self, results: List[Dict], temporal_filter: Dict) -> List[Dict]:
        """Phase 2.4: Boost results that match temporal criteria.
        
        Reorders results to prioritize chunks containing matching time periods.
        """
        if not temporal_filter:
            return results
        
        # Build search patterns from temporal info
        patterns = []
        for q in temporal_filter.get('quarters', []):
            patterns.extend([f'q{q}', f'q {q}', f'quarter {q}'])
        for y in temporal_filter.get('years', []):
            patterns.append(y)
        for fy in temporal_filter.get('fiscal_years', []):
            patterns.extend([f'fy {fy}', f'fy{fy}', fy])
        
        if not patterns:
            return results
        
        # Score each result by temporal matches (check text AND filename)
        def temporal_score(result):
            text = result.get('text', '').lower()
            source_id = result.get('source_id', '').lower()
            filename = result.get('filename', '').lower()
            
            # Combine all searchable text
            searchable = f"{text} {source_id} {filename}"
            
            score = 0
            for pattern in patterns:
                if pattern.lower() in searchable:
                    score += 1
            return score
        
        # Sort by temporal score (descending), then by original order
        scored = [(temporal_score(r), i, r) for i, r in enumerate(results)]
        scored.sort(key=lambda x: (-x[0], x[1]))
        
        boosted = [r for _, _, r in scored]
        
        # Log if we boosted anything
        top_scores = [s for s, _, _ in scored[:5]]
        if any(s > 0 for s in top_scores):
            print(f"[RAG] Temporal boost applied: top scores = {top_scores}")
        else:
            # Warn if temporal filter was specified but no matches found
            print(f"[RAG] WARNING: Temporal filter {patterns} found no matching documents")
        
        return boosted
    
    def _ensure_source_diversity(self, results: List[Dict], min_sources: int = 2) -> List[Dict]:
        """Phase 2.3: Ensure results come from multiple sources when possible.
        
        Reorders results to ensure diversity of sources in top results,
        preventing all citations from coming from a single document.
        """
        if len(results) <= min_sources:
            return results
        
        # Group by source
        by_source = {}
        for r in results:
            source_id = r.get('source_id', 'unknown')
            if source_id not in by_source:
                by_source[source_id] = []
            by_source[source_id].append(r)
        
        # If only one source, return as-is
        if len(by_source) <= 1:
            return results
        
        # Round-robin selection to ensure diversity
        diverse_results = []
        source_iterators = {k: iter(v) for k, v in by_source.items()}
        
        while len(diverse_results) < len(results):
            added_this_round = False
            for source_id in list(source_iterators.keys()):
                try:
                    result = next(source_iterators[source_id])
                    diverse_results.append(result)
                    added_this_round = True
                except StopIteration:
                    del source_iterators[source_id]
            
            if not added_this_round:
                break
        
        return diverse_results
    
    def _verify_retrieval_quality(self, results: List[Dict], analysis: Dict) -> Tuple[bool, str]:
        """Verify that retrieved chunks actually contain relevant data.
        
        Returns: (is_good, reason)
        - is_good: True if retrieval looks good
        - reason: Explanation if retrieval is poor
        """
        if not results:
            return False, "No results retrieved"
        
        # Check if key entities are present in results
        entities = analysis.get("entities", [])
        time_periods = analysis.get("time_periods", [])
        key_metric = analysis.get("key_metric", "")
        
        combined_text = " ".join(r.get("text", "") for r in results[:4]).lower()
        
        # Check entity coverage
        entity_found = False
        for entity in entities:
            if entity.lower() in combined_text:
                entity_found = True
                break
        
        # Check time period coverage (flexible matching for Q1 vs Q 1, FY variations)
        time_found = False
        for period in time_periods:
            period_lower = period.lower()
            # Direct match
            if period_lower in combined_text:
                time_found = True
                break
            # Handle "Q1 2026" matching "Q 1 FY 2026" - extract quarter and year
            import re
            q_match = re.search(r'q\s*(\d)', period_lower)
            y_match = re.search(r'20\d{2}', period_lower)
            if q_match and y_match:
                quarter = q_match.group(1)
                year = y_match.group(0)
                # Check for variations: "q 1 fy 2026", "q1 2026", "q1 fy 2026"
                if f"q {quarter}" in combined_text and year in combined_text:
                    time_found = True
                    break
                if f"q{quarter}" in combined_text and year in combined_text:
                    time_found = True
                    break
        
        # Check metric coverage (flexible: demos matches demo, trials matches trial, etc.)
        metric_found = True
        if key_metric:
            metric_lower = key_metric.lower()
            # Check exact match or singular/plural variations
            metric_found = (
                metric_lower in combined_text or
                metric_lower.rstrip('s') in combined_text or  # demos -> demo
                (metric_lower + 's') in combined_text  # demo -> demos
            )
        
        # Determine quality
        if entities and not entity_found:
            return False, f"Entity '{entities[0]}' not found in top results"
        if time_periods and not time_found:
            return False, f"Time period '{time_periods[0]}' not found in top results"
        if key_metric and not metric_found:
            return False, f"Metric '{key_metric}' not found in top results"
        
        return True, "Retrieval looks good"
    
    async def _adaptive_search(self, table, question: str, query_embedding: List[float], 
                                analysis: Dict, top_k: int) -> List[Dict]:
        """Adaptive search with multiple strategies and verification.
        
        Tries different search strategies until good results are found.
        """
        strategies_tried = []
        
        # Strategy 1: Standard hybrid search with expanded query
        expanded_query = self._build_search_query(analysis, question)
        if HAS_BM25:
            results = self._hybrid_search(expanded_query, table, query_embedding, k=top_k * 2)
        else:
            results = table.search(query_embedding).limit(top_k * 2).to_list()
        
        is_good, reason = self._verify_retrieval_quality(results, analysis)
        strategies_tried.append(("hybrid", is_good, reason))
        
        if is_good:
            print(f"[RAG] Adaptive search: Strategy 1 (hybrid) succeeded")
            return results
        
        print(f"[RAG] Adaptive search: Strategy 1 failed - {reason}")
        
        # Strategy 2: Entity-focused search
        # Search specifically for chunks containing the entities
        entities = analysis.get("entities", [])
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
                    # Re-rank entity matches by relevance to query
                    if HAS_BM25:
                        corpus = [doc.get("text", "").lower().split() for doc in entity_matches]
                        bm25 = BM25Okapi(corpus)
                        scores = bm25.get_scores(expanded_query.lower().split())
                        ranked_indices = np.argsort(scores)[::-1][:top_k * 2]
                        results = [entity_matches[i] for i in ranked_indices]
                    else:
                        results = entity_matches[:top_k * 2]
                    
                    is_good, reason = self._verify_retrieval_quality(results, analysis)
                    strategies_tried.append(("entity_focused", is_good, reason))
                    
                    if is_good:
                        print(f"[RAG] Adaptive search: Strategy 2 (entity-focused) succeeded")
                        return results
                    
                    print(f"[RAG] Adaptive search: Strategy 2 failed - {reason}")
            except Exception as e:
                print(f"[RAG] Adaptive search: Strategy 2 error - {e}")
        
        # Strategy 3: Time-period focused search
        time_periods = analysis.get("time_periods", [])
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
                    # Re-rank by BM25
                    if HAS_BM25 and len(time_matches) > 1:
                        corpus = [doc.get("text", "").lower().split() for doc in time_matches]
                        bm25 = BM25Okapi(corpus)
                        scores = bm25.get_scores(expanded_query.lower().split())
                        ranked_indices = np.argsort(scores)[::-1][:top_k * 2]
                        results = [time_matches[i] for i in ranked_indices if i < len(time_matches)]
                    else:
                        results = time_matches[:top_k * 2]
                    
                    is_good, reason = self._verify_retrieval_quality(results, analysis)
                    strategies_tried.append(("time_focused", is_good, reason))
                    
                    if is_good:
                        print(f"[RAG] Adaptive search: Strategy 3 (time-focused) succeeded")
                        return results
                    
                    print(f"[RAG] Adaptive search: Strategy 3 failed - {reason}")
            except Exception as e:
                print(f"[RAG] Adaptive search: Strategy 3 error - {e}")
        
        # Strategy 4: Full-text scan with keyword matching
        # Last resort - scan all docs for any relevant keywords
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
                keyword_matches.sort(key=lambda x: -x[0])
                results = [doc for _, doc in keyword_matches[:top_k * 2]]
                strategies_tried.append(("keyword_scan", True, "Found keyword matches"))
                print(f"[RAG] Adaptive search: Strategy 4 (keyword scan) found {len(results)} matches")
                return results
        except Exception as e:
            print(f"[RAG] Adaptive search: Strategy 4 error - {e}")
        
        # Return best results we have
        print(f"[RAG] Adaptive search: All strategies tried: {strategies_tried}")
        return results
    
    def _classify_query(self, question: str) -> str:
        """Classify query type for optimal prompt and model selection.
        
        Returns: 'factual', 'synthesis', or 'complex'
        - factual: Simple lookups, counts, specific data extraction
        - synthesis: Summaries, explanations, general questions  
        - complex: Comparisons, analysis, multi-step reasoning
        """
        q_lower = question.lower()
        
        # FACTUAL: Questions asking for specific data/counts/values
        factual_patterns = [
            'how many', 'how much', 'what is the', 'what was the',
            'when did', 'when was', 'who is', 'who was', 'who did',
            'what date', 'what time', 'what number', 'what percentage',
            'list the', 'name the', 'count of', 'total of',
            'did chris', 'did christopher',  # Specific to user's data
        ]
        for pattern in factual_patterns:
            if pattern in q_lower:
                return 'factual'
        
        # COMPLEX: Questions requiring deep analysis
        complex_patterns = [
            'compare', 'contrast', 'analyze', 'explain why', 'explain how',
            'what are the differences', 'what are the similarities',
            'synthesize', 'evaluate', 'assess',
            'pros and cons', 'advantages and disadvantages',
            'step by step', 'walk me through', 'break down',
            'relationship between', 'implications', 'consequences',
            'argue', 'debate', 'critique', 'review'
        ]
        for pattern in complex_patterns:
            if pattern in q_lower:
                return 'complex'
        
        # Long questions or multiple questions = complex
        if len(question) > 100 or question.count('?') > 1:
            return 'complex'
        
        # Default to synthesis (general questions)
        return 'synthesis'
    
    def _should_auto_upgrade_to_think(self, question: str) -> bool:
        """Invisible auto-routing: detect if a 'fast' query should be upgraded to 'think' mode."""
        return self._classify_query(question) == 'complex'
    
    def _check_answer_quality(self, question: str, answer: str, query_type: str) -> Tuple[bool, str]:
        """Lightweight quality check for answers - no LLM call, just heuristics.
        
        Returns: (is_good, reason)
        
        Phase 1: Heuristic checks only (fast, no latency)
        Phase 2 (future): Add LLM-based critique for edge cases
        """
        import re
        
        if not answer or len(answer.strip()) < 15:
            return False, "Answer too short"
        
        # Explicit failure indicators
        failure_phrases = [
            "i cannot find", "not in the sources", "no information",
            "unable to find", "don't have", "doesn't contain",
            "not mentioned", "no data", "cannot determine"
        ]
        answer_lower = answer.lower()
        for phrase in failure_phrases:
            if phrase in answer_lower:
                return False, f"Answer indicates failure: '{phrase}'"
        
        # For factual queries, check if answer contains a number
        if query_type == 'factual':
            has_number = bool(re.search(r'\d+', answer))
            if not has_number:
                return False, "Factual query but no number in answer"
            # Check for "X number" placeholder pattern
            if re.search(r'\bX\s+(number|demos?|seedings?|activities?|total)\b', answer, re.IGNORECASE):
                return False, "Answer contains 'X' placeholder instead of actual number"
        
        # Check for placeholder artifacts that slipped through
        if '[N]' in answer or '[Summary]' in answer:
            return False, "Answer contains placeholder artifacts"
        
        # Check for "Note to user" meta-commentary
        if "note to user" in answer_lower or "replace 'x'" in answer_lower:
            return False, "Answer contains meta-commentary instead of actual data"
        
        return True, "Answer looks good"
    
    async def _generate_query_variants(self, question: str) -> List[str]:
        """Generate variant queries to improve retrieval on retry.
        
        Uses simple transformations - no LLM call for speed.
        Phase 2 (future): Use LLM to generate smarter variants.
        """
        variants = [question]  # Always include original
        
        q_lower = question.lower()
        
        # Variant 1: Expand abbreviations
        expanded = question
        expansions = {
            'q1': 'first quarter Q1',
            'q2': 'second quarter Q2', 
            'q3': 'third quarter Q3',
            'q4': 'fourth quarter Q4',
            'fy': 'fiscal year FY',
        }
        for abbrev, full in expansions.items():
            if abbrev in q_lower:
                expanded = re.sub(rf'\b{abbrev}\b', full, question, flags=re.IGNORECASE)
                if expanded != question:
                    variants.append(expanded)
                    break
        
        # Variant 2: Add "total" or "count" for numeric queries
        if any(word in q_lower for word in ['how many', 'how much', 'total', 'count']):
            variants.append(f"{question} total count number")
        
        # Variant 3: Extract and emphasize key entity
        # Simple heuristic: capitalize words are likely entities
        import re
        entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', question)
        if entities:
            entity_focused = f"{entities[0]} {question}"
            variants.append(entity_focused)
        
        return variants[:3]  # Max 3 variants
    
    async def _corrective_retrieval(self, table, question: str, analysis: Dict, 
                                     top_k: int, original_results: List[Dict]) -> List[Dict]:
        """Corrective retrieval using query variants when initial retrieval fails.
        
        Called when answer quality check fails. Generates variant queries and
        retrieves again to get better results.
        """
        print(f"[RAG] Corrective retrieval triggered - generating query variants")
        
        variants = await self._generate_query_variants(question)
        print(f"[RAG] Query variants: {variants}")
        
        all_results = list(original_results)  # Start with original
        seen_ids = {r.get('chunk_id', r.get('text', '')[:50]) for r in original_results}
        
        for variant in variants[1:]:  # Skip original (index 0)
            # Generate embedding for variant
            embedding = await self._get_embedding(variant)
            
            # Search with variant
            if HAS_BM25:
                variant_results = self._hybrid_search(variant, table, embedding, k=top_k)
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
    
    def _get_prompt_for_query_type(self, query_type: str, num_citations: int, avg_confidence: float = 0.5) -> str:
        """Get optimized prompt based on query classification.
        
        Uses positive, structured prompts based on RAG best practices:
        - Explicit retrieval constraints (answer ONLY from documents)
        - Extractive answering (use exact facts from sources)
        - Clear output format specification
        """
        # Base instruction with strict output format
        base = """You are a helpful assistant. Answer the question using ONLY information from the provided sources.

OUTPUT RULES:
1. Write ONLY your answer - nothing else
2. Put [1], [2], etc. after facts to cite sources
3. Do NOT add sections like "References:", "Sources:", "User context:", or explanations
4. Do NOT explain your reasoning or cite formatting choices
5. If you can't find the answer, just say "I couldn't find this in the documents."

"""

        if query_type == 'factual':
            return base + """Give a direct 1-2 sentence answer with the specific fact or number.

GOOD: "Chris Norman conducted 7 demos in Q1 2026 [1]."
BAD: "Chris Norman conducted 7 demos in Q1 2026 [1]. [References] Source: ..." """

        elif query_type == 'complex':
            return base + """Analyze step by step, then give a clear conclusion in 2-3 paragraphs."""

        else:  # synthesis
            return base + """Provide a clear, direct answer in 1-2 paragraphs."""

    def _extract_mentioned_sources(self, question: str, notebook_id: str) -> List[str]:
        """Extract source IDs if the user mentions specific filenames in their query.
        
        This allows queries like "What does document.xlsx say about X" to filter
        to that specific source for better relevance.
        """
        import re
        
        # Common file extensions to look for - capture full filename
        file_pattern = r'([\w\-\.]+\.(?:xlsx|xls|pdf|docx|doc|pptx|ppt|csv|txt|epub|ipynb|odt|rtf|mp3|wav|mp4|mov))'
        
        mentioned_files = re.findall(file_pattern, question, re.IGNORECASE)
        if not mentioned_files:
            return []
        
        print(f"[RAG] Found filename references in query: {mentioned_files}")
        
        # Get all sources for this notebook
        try:
            sources_data = source_store._load_data()
            notebook_sources = [
                (sid, s) for sid, s in sources_data.get("sources", {}).items()
                if s.get("notebook_id") == notebook_id
            ]
            
            matched_source_ids = []
            for sid, source in notebook_sources:
                filename = source.get("filename", "").lower()
                for mentioned in mentioned_files:
                    # Check if the mentioned filename matches (case-insensitive)
                    if mentioned.lower() in filename or filename in mentioned.lower():
                        matched_source_ids.append(sid)
                        print(f"[RAG] Matched source: '{mentioned}' -> {source.get('filename')} ({sid})")
                        break
            
            return matched_source_ids
        except Exception as e:
            print(f"[RAG] Error extracting mentioned sources: {e}")
            return []

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

        # Step 1: PARALLEL query analysis + embedding (0ms added latency)
        # Run LLM analysis in parallel with embedding generation
        step_start = time.time()
        
        # Check query pattern cache first (instant if cached)
        cache_key = question.lower().strip()[:100]
        cached_analysis = self._query_pattern_cache.get(cache_key)
        
        if cached_analysis:
            query_analysis = cached_analysis
            print(f"[RAG STREAM] Step 1a - Query Analysis (CACHED): 0.00s")
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
                yield {"type": "error", "content": "No documents indexed yet."}
                return
        except Exception as e:
            print(f"[RAG STREAM] Error counting rows: {e}")

        # Send "Searching" status to frontend
        yield {"type": "status", "message": "🔍 Searching your documents..."}

        # Step 2b: Adaptive search with multiple strategies and verification
        step_start = time.time()
        overcollect_k = settings.retrieval_overcollect if self._use_reranker else top_k
        try:
            # Use adaptive search that tries multiple strategies
            results = await self._adaptive_search(
                table, question, query_embedding, query_analysis, overcollect_k
            )
            print(f"[RAG STREAM] Step 2 - Adaptive Search ({len(results)} results): {time.time() - step_start:.2f}s")
        except Exception as e:
            print(f"[RAG STREAM] Search exception: {e}")
            traceback.print_exc()
            yield {"type": "error", "content": f"Search error: {e}"}
            return

        # Filter by source_ids if specified
        if source_ids:
            results = [r for r in results if r["source_id"] in source_ids]

        # Step 2c: Rerank
        if self._use_reranker and len(results) > top_k:
            step_start = time.time()
            results = self.rerank(question, results, top_k=top_k + 1)
            print(f"[RAG STREAM] Step 2c - Reranking ({len(results)} results): {time.time() - step_start:.2f}s")

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

        # Send "Found relevant sections" status with source names
        if citations:
            source_names = list(set(c.get("filename", "document") for c in citations[:3]))
            sources_str = ", ".join(source_names[:2])
            if len(source_names) > 2:
                sources_str += f" and {len(source_names) - 2} more"
            yield {"type": "status", "message": f"📄 Found relevant sections in {sources_str}..."}

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
            yield {"type": "quick_summary", "content": no_info_msg}
            yield {"type": "token", "content": no_info_msg}
            yield {"type": "done", "follow_up_questions": []}
            return

        # Step 4b: Start follow-up generation in background (parallel with main answer)
        followup_task = asyncio.create_task(
            self._generate_follow_up_questions_fast(question, context)
        )

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
        system_prompt = f"User context: {user_context}\n\n{base_prompt}" if user_context else base_prompt

        # Build user prompt with temporal context if detected
        temporal_note = ""
        if temporal_filter:
            periods = []
            for q in temporal_filter.get('quarters', []):
                periods.append(f"Q{q}")
            for y in temporal_filter.get('years', []):
                periods.append(y)
            for fy in temporal_filter.get('fiscal_years', []):
                periods.append(f"FY {fy}")
            if periods:
                temporal_note = f"\n\nIMPORTANT: This question is specifically about {', '.join(periods)}. Only use data from this exact time period."
        
        prompt = f"""Sources:
{context}

Question: {question}{temporal_note}

Answer with [N] citations:"""

        # Two-tier model routing:
        # - System 1 (phi4-mini): Factual queries - fast, reliable
        # - System 2 (olmo-3:7b-instruct): Synthesis/complex queries - thorough, good reasoning
        use_fast_model = (query_type == 'factual') and not deep_think
        
        model_choice = "phi4-mini (fast)" if use_fast_model else "olmo-3:7b-instruct (main)"
        print(f"[RAG STREAM] Query type: {query_type}, using {model_choice}")
        
        # Send status update to frontend (Phase 1.2)
        yield {
            "type": "status",
            "message": f"🤔 {'Analyzing' if query_type == 'complex' else 'Finding answer'}...",
            "query_type": query_type
        }

        step_start = time.time()
        full_answer = ""
        buffer = ""
        references_started = False
        
        # Quick Answer extraction state
        quick_answer_sent = False
        sentence_buffer = ""
        sentence_count = 0
        
        async for token in self._stream_ollama(system_prompt, prompt, deep_think=deep_think, use_fast_model=use_fast_model):
            buffer += token
            full_answer += token
            sentence_buffer += token
            
            # Check if we've hit a References section - stop streaming if so
            if not references_started:
                lower_buffer = buffer.lower()
                for marker in ["\nreferences:", "\nreferences\n", "\nsources:\n", "\ncitations:\n", "\n\n[1] "]:
                    if marker in lower_buffer:
                        references_started = True
                        print(f"[RAG STREAM] Detected references section, stopping output")
                        break
            
            if not references_started:
                # Send token directly - don't clean individual tokens as it strips spaces
                # _clean_llm_output is for complete text, not streaming tokens
                if token:
                    yield {"type": "token", "content": token}
            
            # Extract Quick Answer from first 1-2 complete sentences
            if not quick_answer_sent:
                # Count sentences by looking for sentence endings
                for end_char in ['. ', '.\n', '? ', '?\n', '! ', '!\n']:
                    if end_char in sentence_buffer:
                        sentence_count += sentence_buffer.count(end_char)
                        sentence_buffer = sentence_buffer.split(end_char)[-1]  # Keep remainder
                
                # After 2 sentences (or 1 if it's substantial), emit Quick Answer
                if sentence_count >= 2 or (sentence_count >= 1 and len(full_answer) > 150):
                    # Extract first 1-2 sentences for Quick Answer
                    quick_answer = self._extract_first_sentences(full_answer, max_sentences=2)
                    if quick_answer:
                        # Clean LaTeX artifacts from Quick Answer
                        quick_answer = self._clean_llm_output(quick_answer)
                        yield {"type": "quick_summary", "content": quick_answer}
                        print(f"[RAG STREAM] Quick Answer extracted from stream: {len(quick_answer)} chars")
                        quick_answer_sent = True
        
        # If answer was very short and we never sent Quick Answer, send the whole thing
        if not quick_answer_sent and full_answer.strip():
            yield {"type": "quick_summary", "content": self._clean_llm_output(full_answer.strip())}
        
        print(f"[RAG STREAM] Step 6 - LLM streaming: {time.time() - step_start:.2f}s")

        # Step 7: Wait for follow-up questions
        step_start = time.time()
        follow_up_questions = await followup_task
        print(f"[RAG STREAM] Step 7 - Follow-ups ready: {time.time() - step_start:.2f}s")

        yield {"type": "done", "follow_up_questions": follow_up_questions}

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

        total_time = time.time() - total_start
        print(f"{'='*60}")
        print(f"[RAG STREAM] TOTAL time: {total_time:.2f}s")
        print(f"{'='*60}\n")

    def _clean_llm_output(self, text: str) -> str:
        """Clean up LLM output artifacts.
        
        Minimal post-processing - let the prompt do the heavy lifting.
        Only clean up formatting artifacts that slip through.
        """
        import re
        
        # Remove LaTeX formatting artifacts
        text = re.sub(r'\\boxed\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\textbf\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\textit\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\(boxed|text|textbf|textit)\b', '', text)
        
        # Remove unwanted sections that LLM sometimes adds
        text = re.sub(r'\n*\[?References\]?:?.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\n*Sources?:.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\n*User context:.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\n*Citation.*should not be included.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        
        return text

    def _extract_first_sentences(self, text: str, max_sentences: int = 2) -> str:
        """Extract the first N sentences from text for Quick Answer preview.
        
        This ensures Quick Answer is always consistent with Detailed Answer
        since it's literally extracted from the same response.
        """
        if not text:
            return ""
        
        import re
        
        # Split by sentence endings followed by space or newline
        # But require the sentence to be at least 20 chars to avoid false positives
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        
        if not sentences:
            return text.strip()
        
        # Filter out very short "sentences" that are likely fragments
        # Also skip sentences that end with incomplete patterns like "for." or "of."
        valid_sentences = []
        for s in sentences:
            s = s.strip()
            if len(s) < 15:
                continue
            # Skip if it ends with a preposition + period (incomplete)
            if re.search(r'\b(for|of|to|in|on|at|by|with|from)\.$', s, re.IGNORECASE):
                continue
            valid_sentences.append(s)
        
        if not valid_sentences:
            # Fallback: just take first chunk up to a reasonable length
            first_chunk = text[:300]
            # Find last complete sentence
            last_period = max(first_chunk.rfind('. '), first_chunk.rfind('.\n'))
            if last_period > 50:
                return first_chunk[:last_period + 1].strip()
            return first_chunk.strip() + '...'
        
        # Take first N valid sentences
        result_sentences = valid_sentences[:max_sentences]
        result = ' '.join(result_sentences)
        
        # Ensure it ends with proper punctuation
        if result and result[-1] not in '.!?':
            result += '.'
        
        return result.strip()

    async def generate_proactive_insights(self, notebook_id: str, limit: int = 3) -> List[Dict]:
        """Phase 4.1: Generate proactive insights from document content.
        
        Analyzes document summaries and content to suggest interesting
        questions or observations the user might want to explore.
        """
        try:
            # Get table and sample some content
            table = self._get_table(notebook_id)
            
            # Look for summary chunks first (they have chunk_index = -1)
            try:
                all_rows = table.search([0.0] * settings.embedding_dim).limit(50).to_list()
                summaries = [r for r in all_rows if r.get('chunk_index') == -1]
                regular_chunks = [r for r in all_rows if r.get('chunk_index') != -1][:10]
            except Exception:
                return []
            
            if not summaries and not regular_chunks:
                return []
            
            # Build context from summaries or sample chunks
            if summaries:
                context = "\n\n".join([s.get('text', '')[:500] for s in summaries[:5]])
            else:
                context = "\n\n".join([c.get('text', '')[:300] for c in regular_chunks[:5]])
            
            prompt = f"""Based on these document summaries/excerpts, suggest {limit} interesting questions or insights the user might want to explore.

Documents:
{context}

Generate {limit} insights in this format (one per line):
💡 [Interesting observation or question about the data]

Be specific and actionable. Focus on patterns, comparisons, or notable findings."""

            response = await self._call_ollama(
                "You are a helpful analyst. Generate brief, specific insights.",
                prompt,
                model=settings.ollama_fast_model
            )
            
            insights = []
            for line in response.strip().split('\n'):
                line = line.strip()
                if line and ('💡' in line or line.startswith('-')):
                    # Clean up the line
                    insight = line.replace('💡', '').strip().lstrip('-').strip()
                    if insight and len(insight) > 10:
                        insights.append({
                            "text": insight,
                            "type": "proactive"
                        })
            
            return insights[:limit]
            
        except Exception as e:
            print(f"[RAG] Proactive insights generation failed: {e}")
            return []

    async def _generate_follow_up_questions_fast(self, question: str, context: str, answer: str = "") -> List[str]:
        """Phase 3.4: Generate contextual follow-up questions using fast model.
        
        Enhanced to consider the answer given, making follow-ups more relevant
        and encouraging deeper exploration of the topic.
        """
        try:
            # More contextual prompt that considers the answer
            system_prompt = """Generate exactly 3 follow-up questions that would help the user explore this topic deeper.
Questions should:
- Build on what was just answered
- Explore related aspects not yet covered
- Be specific and actionable
Output ONLY the questions, one per line. No numbering, no preamble."""
            prompt = f"Topic: {question}\n\nContext: {context[:1000]}\n\n3 questions:"
            
            response = await self._call_ollama(system_prompt, prompt, model=settings.ollama_fast_model)
            
            questions = []
            for line in response.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                # Strip numbering/bullets
                line = line.lstrip('0123456789.-) ')
                # Only keep lines that end with ? (actual questions)
                if line.endswith('?'):
                    questions.append(line)
            
            return questions[:3] if questions else []
        except Exception as e:
            print(f"Failed to generate follow-up questions: {e}")
            return []

    async def get_suggested_questions(self, notebook_id: str) -> List[str]:
        """Tier 3: Generate suggested questions based on actual document content.
        
        Uses document summaries (if available) or sample chunks to generate
        relevant questions the user might want to ask.
        """
        try:
            table = self._get_table(notebook_id)
            
            # Try to get document summaries first (chunk_index = -1)
            try:
                all_rows = table.search([0.0] * settings.embedding_dim).limit(30).to_list()
                summaries = [r for r in all_rows if r.get('chunk_index') == -1]
                regular_chunks = [r for r in all_rows if r.get('chunk_index') != -1][:5]
            except Exception:
                return self._default_suggested_questions()
            
            if not summaries and not regular_chunks:
                return self._default_suggested_questions()
            
            # Build context from summaries or sample chunks
            if summaries:
                context = "\n\n".join([s.get('text', '')[:400] for s in summaries[:3]])
            else:
                context = "\n\n".join([c.get('text', '')[:300] for c in regular_chunks[:3]])
            
            # Use fast model to generate questions quickly
            prompt = f"""Based on these document excerpts, generate 3 specific questions a user might want to ask.

Documents:
{context}

Generate exactly 3 questions, one per line. Questions should be specific to the content, not generic.
No numbering, no preamble, just the questions."""

            response = await self._call_ollama(
                "Generate 3 specific questions based on document content. Output only questions, one per line.",
                prompt,
                model=settings.ollama_fast_model
            )
            
            # Parse questions from response
            questions = []
            for line in response.strip().split('\n'):
                line = line.strip()
                # Skip empty lines and lines that look like numbering
                if line and not line[0].isdigit() and '?' in line:
                    # Clean up the line
                    question = line.lstrip('- •').strip()
                    if question and len(question) > 10:
                        questions.append(question)
            
            # Return parsed questions or defaults
            return questions[:3] if questions else self._default_suggested_questions()
            
        except Exception as e:
            print(f"[RAG] Suggested questions generation failed: {e}")
            return self._default_suggested_questions()
    
    def _default_suggested_questions(self) -> List[str]:
        """Fallback suggested questions"""
        return [
            "What are the main topics covered in my documents?",
            "Can you summarize the key points?",
            "What are the most important findings?"
        ]

    async def _generate_answer(self, question: str, context: str, num_citations: int = 5, llm_provider: Optional[str] = None, notebook_id: Optional[str] = None, conversation_id: Optional[str] = None, deep_think: bool = False) -> Dict:
        """Generate answer using LLM with memory augmentation and user personalization. Returns dict with answer and memory_used info."""
        
        # If no citations/context, refuse to answer to prevent hallucination
        if num_citations == 0 or not context.strip():
            return {
                "answer": "I don't have enough relevant information in your documents to answer this question accurately. Try uploading more documents related to this topic, or rephrase your question.",
                "memory_used": [],
                "memory_context_summary": None
            }
        
        # Check if memory is enabled (frontend can disable via localStorage)
        memory_used = []
        
        # Get user profile for personalization
        from api.settings import get_user_profile_sync, build_user_context
        user_profile = get_user_profile_sync()
        user_context = build_user_context(user_profile)
        
        # Get memory context to augment the prompt
        memory_context = await memory_agent.get_memory_context(
            query=question,
            notebook_id=notebook_id,
            max_tokens=500  # Reserve tokens for memory
        )
        
        # Build system prompt with universal guardrails
        universal_rules = """STRICT RULES:
- State facts confidently - NEVER use hedging phrases like "however", "it should be noted"
- Put citations inline like [1] or [2] - NEVER write [N] or [Summary]
- NEVER create a "Citation Numbers:" section or list citations separately
- NEVER add a References section at the end"""

        base_prompt = f"""Answer directly in 1-2 paragraphs with inline citations.

{universal_rules}

Highlight key insights from the sources."""
        
        # Add Deep Think chain-of-thought instructions
        if deep_think:
            base_prompt = f"""Analyze step by step, then give a clear conclusion.

{universal_rules}

Be thorough but concise (2-3 paragraphs). Inline citations only."""
        
        # Combine user context + memory + base prompt
        system_parts = []
        if user_context:
            system_parts.append(f"User context: {user_context}")
            memory_used.append("user_profile")
        if memory_context.core_memory_block:
            system_parts.append(memory_context.core_memory_block)
            if memory_context.core_memory_block.strip():
                memory_used.append("core_context")
        system_parts.append(base_prompt)
        
        system_prompt = "\n\n".join(system_parts)
        
        # Add retrieved memories to context if available
        memory_section = ""
        if memory_context.retrieved_memories:
            memory_section = "\n\nRelevant past context:\n" + "\n".join(memory_context.retrieved_memories) + "\n"
            memory_used.append("retrieved_memories")

        prompt = f"""Sources:
{context}
{memory_section}
Q: {question}

Answer concisely with inline [N] citations:"""

        # Determine which provider to use
        provider = llm_provider or settings.llm_provider

        # Call LLM based on provider
        if provider == "ollama":
            answer = await self._call_ollama(system_prompt, prompt)
        elif provider == "openai":
            answer = await self._call_openai(system_prompt, prompt)
        elif provider == "anthropic":
            answer = await self._call_anthropic(system_prompt, prompt)
        else:
            answer = await self._call_ollama(system_prompt, prompt)  # Default to ollama
        
        return {
            "answer": answer,
            "memory_used": memory_used,
            "memory_context_summary": memory_context.core_memory_block[:200] if memory_context.core_memory_block else None
        }
    
    async def _call_ollama(self, system_prompt: str, prompt: str, model: str = None) -> str:
        """Call Ollama API"""
        # Use very long timeout - LLM generation can take minutes for complex queries
        timeout = httpx.Timeout(10.0, read=600.0)  # 10s connect, 10 min read
        # Default to fast model for non-streaming calls - faster response times
        # Main model (olmo-3:7b-instruct) used for streaming queries
        use_model = model or settings.ollama_fast_model
        async with httpx.AsyncClient(timeout=timeout) as client:
            print(f"Calling Ollama with model: {use_model}")
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": use_model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": False,
                    "keep_alive": -1,  # Keep model loaded (Tier 1 optimization)
                    "options": {
                        "num_predict": 500,  # Increased for better responses
                    }
                }
            )
            result = response.json()
            print(f"Ollama response received, length: {len(result.get('response', ''))}")
            return result.get("response", "No response from LLM")

    async def _stream_ollama(self, system_prompt: str, prompt: str, deep_think: bool = False, use_fast_model: bool = False) -> AsyncGenerator[str, None]:
        """Stream response from Ollama API with stop sequences to prevent citation lists
        
        Args:
            deep_think: Use CoT prompting with lower temperature for thorough analysis
            use_fast_model: Use phi4-mini (System 1) instead of olmo-3:7b-instruct (System 2)
        """
        timeout = httpx.Timeout(10.0, read=600.0)
        
        # Two-tier model selection:
        # - System 1 (phi4-mini): Factual queries, fast responses
        # - System 2 (olmo-3:7b-instruct): Synthesis, complex queries, Deep Think
        if use_fast_model and not deep_think:
            model = settings.ollama_fast_model
            temperature = 0.7
            top_p = 0.9
        else:
            model = settings.ollama_model
            # Lower temperature for Deep Think mode (more focused reasoning)
            temperature = 0.5 if deep_think else 0.7
            top_p = 0.9
        
        # Stop sequences to prevent LLM from generating citation/reference lists
        stop_sequences = [
            "\n\nReferences",
            "\n\nSources:",
            "\n\nSources\n",
            "\n\nCitations:",
            "\n\nCitations\n",
            "\n\n---\n[",
            "\n\n[1]:",
            "\n\n**References",
            "\n\n**Sources",
        ]
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            mode_str = " [Deep Think]" if deep_think else (" [Fast]" if use_fast_model else "")
            print(f"Streaming from Ollama with model: {model}{mode_str} (temp={temperature}, top_p={top_p})")
            
            # Tier 1 optimizations: keep_alive prevents cold start, num_predict caps runaway generation
            async with client.stream(
                "POST",
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": True,
                    "stop": stop_sequences,
                    "keep_alive": -1,  # Keep model loaded indefinitely (eliminates cold start)
                    "options": {
                        "temperature": temperature,
                        "top_p": top_p,
                        "num_predict": 400 if not deep_think else 800,  # Cap output length
                        "num_ctx": 4096,  # Reasonable context window
                    }
                }
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        import json
                        data = json.loads(line)
                        # olmo-3:7b-instruct streams response tokens directly
                        if data.get("response"):
                            yield data["response"]

    async def _call_openai(self, system_prompt: str, prompt: str) -> str:
        """Call OpenAI API"""
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content

    async def _call_anthropic(self, system_prompt: str, prompt: str) -> str:
        """Call Anthropic API"""
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

        response = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    def _chunk_text_smart(self, text: str, source_type: str, filename: str) -> List[str]:
        """Smart chunking that adapts strategy based on source type.
        
        Different file types need different chunking strategies:
        - Tabular data (xlsx, csv): Keep rows together, include headers in each chunk
        - Documents (pdf, docx): Split by paragraphs/sections
        - Code: Split by functions/classes
        - Transcripts: Split by speaker turns or time segments
        """
        filename_lower = filename.lower()
        
        # Detect tabular data
        is_tabular = source_type in ['xlsx', 'xls', 'csv'] or \
                     filename_lower.endswith(('.xlsx', '.xls', '.csv'))
        
        # Detect if content looks like tabular data (row-based format)
        if not is_tabular and 'Row ' in text[:500] and ': ' in text[:500]:
            is_tabular = True
        
        if is_tabular:
            return self._chunk_tabular_data(text)
        
        # Default: use standard semantic chunking
        return self._chunk_text(text)
    
    def _chunk_tabular_data(self, text: str) -> List[str]:
        """Chunk tabular data keeping related rows together with context.
        
        Strategy:
        1. Extract header/context lines (sheet name, column headers)
        2. Group rows into chunks respecting both row count AND character limits
        3. Prepend header context to each chunk for self-contained retrieval
        """
        # Max chunk size in characters (leave room for embedding model context)
        max_chunk_chars = settings.chunk_size  # Use same limit as regular chunking
        
        lines = text.split('\n')
        
        # Find header/context lines (sheet info, column headers, etc.)
        header_lines = []
        data_lines = []
        
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            
            # Identify header/context lines
            if line_stripped.startswith('===') or \
               line_stripped.startswith('Data from sheet') or \
               line_stripped.startswith('Complete row data') or \
               line_stripped.startswith('This data is from') or \
               ('Column' in line_stripped and ':' in line_stripped and line_stripped.startswith('Row 1:')):
                header_lines.append(line_stripped)
            else:
                data_lines.append(line_stripped)
        
        # Build header context (prepended to each chunk)
        header_context = '\n'.join(header_lines[:5]) if header_lines else ""
        header_len = len(header_context) + 2  # +2 for \n\n separator
        
        # Group data lines into chunks respecting character limits
        chunks = []
        current_chunk_lines = []
        current_chunk_len = header_len
        
        for line in data_lines:
            line_len = len(line) + 1  # +1 for newline
            
            # If adding this line would exceed limit, start new chunk
            if current_chunk_len + line_len > max_chunk_chars and current_chunk_lines:
                # Save current chunk
                if header_context:
                    chunk_text = header_context + '\n\n' + '\n'.join(current_chunk_lines)
                else:
                    chunk_text = '\n'.join(current_chunk_lines)
                chunks.append(chunk_text)
                
                # Start new chunk
                current_chunk_lines = [line]
                current_chunk_len = header_len + line_len
            else:
                current_chunk_lines.append(line)
                current_chunk_len += line_len
        
        # Don't forget the last chunk
        if current_chunk_lines:
            if header_context:
                chunk_text = header_context + '\n\n' + '\n'.join(current_chunk_lines)
            else:
                chunk_text = '\n'.join(current_chunk_lines)
            if chunk_text.strip():
                chunks.append(chunk_text)
        
        # If no chunks created, fall back to standard chunking
        if not chunks:
            return self._chunk_text(text)
        
        print(f"[RAG] Tabular chunking: {len(data_lines)} rows -> {len(chunks)} chunks (max {max_chunk_chars} chars/chunk)")
        return chunks

    def _chunk_text(self, text: str) -> List[str]:
        """Chunk text into smaller pieces with semantic boundary awareness.
        
        Tries to split at paragraph/sentence boundaries rather than mid-sentence
        for better embedding quality. Falls back to character-based splitting.
        """
        chunk_size = settings.chunk_size
        chunk_overlap = settings.chunk_overlap

        # First, try to split by paragraphs (double newlines)
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
        
        chunks = []
        current_chunk = ""
        
        for para in paragraphs:
            # If adding this paragraph would exceed chunk_size
            if len(current_chunk) + len(para) + 2 > chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                
                # If single paragraph is larger than chunk_size, split it
                if len(para) > chunk_size:
                    # Try to split at sentence boundaries
                    sentences = self._split_into_sentences(para)
                    for sentence in sentences:
                        if len(current_chunk) + len(sentence) + 1 > chunk_size:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            # If single sentence is too long, fall back to character split
                            if len(sentence) > chunk_size:
                                chunks.extend(self._char_split(sentence, chunk_size, chunk_overlap))
                                current_chunk = ""
                            else:
                                current_chunk = sentence
                        else:
                            current_chunk = (current_chunk + " " + sentence).strip() if current_chunk else sentence
                else:
                    current_chunk = para
            else:
                current_chunk = (current_chunk + "\n\n" + para).strip() if current_chunk else para
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        # If we got no chunks (empty text), return empty list
        if not chunks:
            return []
        
        # Add overlap by including end of previous chunk at start of next
        if chunk_overlap > 0 and len(chunks) > 1:
            overlapped_chunks = [chunks[0]]
            for i in range(1, len(chunks)):
                prev_end = chunks[i-1][-chunk_overlap:] if len(chunks[i-1]) > chunk_overlap else chunks[i-1]
                overlapped_chunks.append(prev_end + "\n" + chunks[i])
            chunks = overlapped_chunks
        
        return chunks
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        import re
        # Split on sentence-ending punctuation followed by space or end
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]
    
    def _char_split(self, text: str, chunk_size: int, overlap: int) -> List[str]:
        """Fallback character-based splitting for very long text without boundaries."""
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start = end - overlap
        return chunks

    def _get_parent_context(self, chunks: List[str], chunk_index: int, max_parent_chars: int = 2000) -> str:
        """v0.60: Get expanded parent context for a chunk.
        
        Combines the current chunk with surrounding chunks to provide
        more context during retrieval. This helps the LLM understand
        the broader context of a matched chunk.
        
        Args:
            chunks: All chunks from the document
            chunk_index: Index of the current chunk
            max_parent_chars: Maximum characters for parent context
            
        Returns:
            Parent text containing current chunk + surrounding context
        """
        if not chunks or chunk_index < 0 or chunk_index >= len(chunks):
            return ""
        
        current_chunk = chunks[chunk_index]
        
        # Start with current chunk
        parent_parts = [current_chunk]
        current_len = len(current_chunk)
        
        # Add previous chunks until we hit the limit
        prev_idx = chunk_index - 1
        while prev_idx >= 0 and current_len < max_parent_chars:
            prev_chunk = chunks[prev_idx]
            if current_len + len(prev_chunk) > max_parent_chars:
                # Add partial chunk
                remaining = max_parent_chars - current_len
                parent_parts.insert(0, prev_chunk[-remaining:] + "...")
                break
            parent_parts.insert(0, prev_chunk)
            current_len += len(prev_chunk)
            prev_idx -= 1
        
        # Add next chunks until we hit the limit
        next_idx = chunk_index + 1
        while next_idx < len(chunks) and current_len < max_parent_chars:
            next_chunk = chunks[next_idx]
            if current_len + len(next_chunk) > max_parent_chars:
                # Add partial chunk
                remaining = max_parent_chars - current_len
                parent_parts.append("..." + next_chunk[:remaining])
                break
            parent_parts.append(next_chunk)
            current_len += len(next_chunk)
            next_idx += 1
        
        return "\n\n".join(parent_parts)

    def get_current_embedding_dim(self) -> int:
        """Get the dimension of the current embedding model"""
        test_embedding = self.encode("test")[0]
        return len(test_embedding)

    def check_embedding_dimension_mismatch(self) -> List[str]:
        """Check all notebook tables for embedding dimension mismatch.
        Returns list of notebook IDs that need re-indexing."""
        if self.db is None:
            self.db = lancedb.connect(str(self.db_path))
        
        current_dim = self.get_current_embedding_dim()
        mismatched_notebooks = []
        
        for table_name in self.db.table_names():
            if table_name.startswith("notebook_"):
                try:
                    table = self.db.open_table(table_name)
                    stored_dim = self._get_stored_vector_dim(table)
                    if stored_dim is not None and stored_dim != current_dim:
                        notebook_id = table_name.replace("notebook_", "")
                        mismatched_notebooks.append(notebook_id)
                        print(f"[RAG] Dimension mismatch: {table_name} has {stored_dim}-dim vectors, current model uses {current_dim}-dim")
                except Exception as e:
                    print(f"[RAG] Error checking {table_name}: {e}")
        
        return mismatched_notebooks


# Global instance
rag_engine = RAGEngine()
