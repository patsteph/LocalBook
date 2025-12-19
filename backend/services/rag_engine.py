"""RAG (Retrieval Augmented Generation) Engine"""
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
from models.knowledge_graph import ConceptExtractionRequest
from models.memory import MemoryExtractionRequest
from services.memory_agent import memory_agent
from storage.source_store import source_store


_concept_extraction_semaphore = asyncio.Semaphore(int(os.getenv("LOCALBOOK_KG_CONCURRENCY", "4")))  # Increased from 2 to 4

# Note: Debouncing removed - we now await extraction directly in ingest_document

# Shared thread pool for LanceDB operations (avoids creating per-query)
_search_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lancedb_search")


class RAGEngine:
    """RAG engine for document Q&A"""

    def __init__(self):
        self.db_path = settings.db_path
        self.embedding_model = None  # Lazy load (for sentence-transformers fallback)
        self.reranker = None  # Lazy load cross-encoder for reranking
        self.db = None
        self._use_ollama_embeddings = settings.use_ollama_embeddings
        self._use_reranker = settings.use_reranker
    
    def _get_reranker(self):
        """Lazy load the cross-encoder reranker model"""
        if self.reranker is None:
            from sentence_transformers import CrossEncoder
            reranker_model = settings.reranker_model
            self.reranker = CrossEncoder(reranker_model, max_length=512)
            print(f"[RAG] Loaded reranker: {reranker_model}")
        return self.reranker
    
    def _load_reranker(self):
        """Force load the reranker model (used for warmup)"""
        if self._use_reranker:
            return self._get_reranker()
        return None
    
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """Rerank documents using cross-encoder for better relevance"""
        if not documents:
            return documents
        
        reranker = self._get_reranker()
        
        # Create query-document pairs
        pairs = [(query, doc.get("text", "")) for doc in documents]
        
        # Score all pairs
        scores = reranker.predict(pairs)
        
        # Add scores to documents and sort
        for doc, score in zip(documents, scores):
            doc["rerank_score"] = float(score)
        
        # Sort by rerank score (higher is better) and take top_k
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
        """Get embeddings for multiple texts from Ollama synchronously"""
        embeddings = []
        for text in texts:
            embedding = self._get_ollama_embedding_sync(text)
            embeddings.append(embedding)
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
            placeholder_embedding = self.encode("placeholder")[0].tolist()
            self.db.create_table(
                table_name,
                data=[{
                    "vector": placeholder_embedding,
                    "text": "placeholder",
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
                # LanceDB cosine distance is 0-2 range for normalized vectors
                distance = result.get("_distance", 1.0)
                # Convert to confidence: 0 dist = 100%, 1.0 dist = 50%, 2.0 dist = 0%
                confidence = max(0, min(1, 1 - (distance / 2)))
                print(f"{log_prefix} Citation {i+1}: distance={distance:.2f} -> confidence={confidence:.0%}")
            
            confidence_level = "high" if confidence >= 0.6 else "medium" if confidence >= 0.4 else "low"
            
            all_citations.append({
                "number": i + 1,
                "source_id": result.get("source_id", "unknown"),
                "filename": source_filenames.get(result.get("source_id", ""), "Unknown"),
                "chunk_index": result.get("chunk_index", 0),
                "text": text,
                "snippet": text[:150] + "..." if len(text) > 150 else text,
                "page": result.get("metadata", {}).get("page") if isinstance(result.get("metadata"), dict) else None,
                "confidence": round(confidence, 2),
                "confidence_level": confidence_level
            })

        # Only filter out truly irrelevant results (< 35% confidence)
        quality_citations = [c for c in all_citations if c["confidence"] >= 0.35]
        
        # Check if ALL citations are very low confidence (< 20%) - this means we have no relevant sources
        max_confidence = max((c["confidence"] for c in all_citations), default=0)
        very_low_confidence = max_confidence < 0.20
        
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
        numbered_context = [f"[{i+1}] {c['text']}" for i, c in enumerate(quality_citations)]
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

        # Chunk the text
        chunks = self._chunk_text(text)

        # Generate embeddings
        embeddings = self.encode(chunks)

        # Prepare data for insertion with metadata
        data = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            data.append({
                "vector": embedding.tolist(),
                "text": chunk,
                "source_id": source_id,
                "chunk_index": i,
                "filename": filename,
                "source_type": source_type
            })

        # Insert into LanceDB
        table = self._get_table(notebook_id)
        table.add(data)

        # Extract concepts for knowledge graph
        # NOTE: We await this directly instead of using asyncio.create_task() because
        # fire-and-forget tasks may not complete in the embedded Tauri backend context.
        # This makes uploads slightly slower but guarantees concept extraction happens.
        print(f"[RAG] About to extract concepts for source {source_id} ({len(chunks)} chunks)")
        try:
            await self._extract_concepts_for_source(
                notebook_id=notebook_id,
                source_id=source_id,
                chunks=chunks
            )
            print(f"[RAG] Concept extraction completed for source {source_id}")
        except Exception as e:
            import traceback
            print(f"[RAG] CRITICAL: Concept extraction failed for source {source_id}: {e}")
            traceback.print_exc()

        return {
            "source_id": source_id,
            "chunks": len(chunks),
            "characters": len(text)
        }

    async def _extract_concepts_for_source(
        self,
        notebook_id: str,
        source_id: str,
        chunks: List[str]
    ):
        """Extract concepts from document chunks for knowledge graph.
        
        Now awaited directly during ingest_document to guarantee execution.
        Uses semaphore to limit concurrent LLM calls.
        """
        async with _concept_extraction_semaphore:
            try:
                from services.knowledge_graph import knowledge_graph_service
                from api.constellation_ws import notify_concept_added, notify_build_progress, notify_build_complete
                
                print(f"[KG] Starting concept extraction for source {source_id} ({len(chunks)} chunks)")
                
                total_concepts = 0
                
                # Process ALL chunks by batching 3 consecutive chunks into one LLM call
                # This gives full coverage while keeping LLM calls manageable
                batch_size = 3
                batches = []
                for i in range(0, len(chunks), batch_size):
                    batch_text = "\n\n---\n\n".join(chunks[i:i+batch_size])
                    batches.append((i, batch_text))
                
                for idx, (chunk_start_idx, batch_text) in enumerate(batches):
                    request = ConceptExtractionRequest(
                        text=batch_text,
                        source_id=source_id,
                        chunk_index=chunk_start_idx,
                        notebook_id=notebook_id
                    )
                    
                    result = await knowledge_graph_service.extract_concepts(request)
                    if result.concepts:
                        total_concepts += len(result.concepts)
                        print(f"[KG] Batch {idx}: extracted {len(result.concepts)} concepts, {len(result.links)} links")
                        
                        # Broadcast each new concept via WebSocket
                        for concept in result.concepts:
                            await notify_concept_added({
                                "name": concept.name,
                                "notebook_id": notebook_id,
                                "source_id": source_id
                            })
                    
                    # Broadcast progress
                    progress = (idx + 1) / len(batches) * 100
                    await notify_build_progress({
                        "source_id": source_id,
                        "progress": round(progress, 1),
                        "concepts_found": total_concepts
                    })
                
                print(f"[KG] Concept extraction complete for source {source_id}: {total_concepts} concepts")
                
                # Fire build_complete after each source extraction
                # This triggers clustering and UI refresh
                await notify_build_complete()
                
            except Exception as e:
                import traceback
                print(f"[KG] Concept extraction error: {e}")
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

        # Step 1: Generate query embedding
        step_start = time.time()
        query_embedding = self.encode(question)[0].tolist()
        print(f"[RAG] Step 1 - Embedding: {time.time() - step_start:.2f}s")

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

        # Step 3: Vector search
        step_start = time.time()
        overcollect_k = settings.retrieval_overcollect if self._use_reranker else top_k
        try:
            results = table.search(query_embedding).limit(overcollect_k).to_list()
            print(f"[RAG] Step 2 - Search ({len(results)} results): {time.time() - step_start:.2f}s")
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
        answer_result = await self._generate_answer(question, context, num_citations, llm_provider, notebook_id, conversation_id)
        answer = answer_result["answer"]
        memory_used = answer_result.get("memory_used", [])
        memory_context_summary = answer_result.get("memory_context_summary")
        print(f"[RAG] Step 5 - LLM answer: {time.time() - step_start:.2f}s")

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

    def _should_auto_upgrade_to_think(self, question: str) -> bool:
        """Invisible auto-routing: detect if a 'fast' query should be upgraded to 'think' mode.
        
        This allows simple questions to stay fast while complex ones get better reasoning,
        without requiring the user to manually toggle modes.
        """
        q_lower = question.lower()
        
        # Complexity signals that warrant deeper thinking
        complexity_keywords = [
            'compare', 'contrast', 'analyze', 'explain why', 'explain how',
            'what are the differences', 'what are the similarities',
            'summarize', 'synthesize', 'evaluate', 'assess',
            'pros and cons', 'advantages and disadvantages',
            'step by step', 'walk me through', 'break down',
            'relationship between', 'how does', 'why does',
            'implications', 'consequences', 'impact of',
            'argue', 'debate', 'critique', 'review'
        ]
        
        # Check for complexity keywords
        for keyword in complexity_keywords:
            if keyword in q_lower:
                return True
        
        # Long questions (>100 chars) often need more thought
        if len(question) > 100:
            return True
        
        # Multiple question marks suggest compound questions
        if question.count('?') > 1:
            return True
        
        return False

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

        # Step 1: Generate query embedding
        step_start = time.time()
        query_embedding = self.encode(question)[0].tolist()
        print(f"[RAG STREAM] Step 1 - Embedding: {time.time() - step_start:.2f}s")

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

        # Step 2b: Vector search (using shared thread pool)
        step_start = time.time()
        overcollect_k = settings.retrieval_overcollect if self._use_reranker else top_k
        try:
            def do_search():
                return table.search(query_embedding).limit(overcollect_k).to_list()
            
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(_search_executor, do_search)
            print(f"[RAG STREAM] Step 2 - Search ({len(results)} results): {time.time() - step_start:.2f}s")
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

        # Step 3: Build citations and context (shared helper)
        step_start = time.time()
        citations, sources, context, low_confidence = await self._build_citations_and_context(results, "[RAG STREAM]")
        num_citations = len(citations)
        print(f"[RAG STREAM] Step 3 - Build context: {time.time() - step_start:.2f}s ({len(context)} chars)")

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

        # Step 4b: Generate quick summary
        step_start = time.time()
        quick_summary = await self._generate_quick_summary(question, context, num_citations)
        print(f"[RAG STREAM] Step 4 - Quick summary: {time.time() - step_start:.2f}s")
        
        yield {"type": "quick_summary", "content": quick_summary}

        # Step 5: Start follow-up generation in background (parallel with main answer)
        followup_task = asyncio.create_task(
            self._generate_follow_up_questions_fast(question, context)
        )

        # Step 6: Stream the detailed answer
        from api.settings import get_user_profile_sync, build_user_context
        user_profile = get_user_profile_sync()
        user_context = build_user_context(user_profile)
        
        # Build system prompt based on mode
        if deep_think:
            base_prompt = f"""You are in Deep Think mode. Think through this problem step by step:
1. First, identify the key aspects of the question
2. Consider relevant information from the sources
3. Reason through the implications
4. Synthesize your findings into a clear, well-reasoned answer

Answer using sources [1]-[{num_citations}]. Use inline citations like [1], [2] within sentences. Be thorough and analytical.
IMPORTANT: Do NOT add a References or Sources section at the end. Only use inline citations.
IMPORTANT: Only answer based on the provided sources. If the sources don't contain relevant information, say so honestly.
NEVER make up quotes or attribute statements to people unless those exact quotes appear in the sources. Do not fabricate attributions."""
        else:
            base_prompt = f"""Answer using sources [1]-[{num_citations}]. Be concise (1-2 paragraphs). Use inline citations like [1], [2] within sentences.
IMPORTANT: Do NOT add a References or Sources section at the end. Only use inline citations.
IMPORTANT: Only answer based on the provided sources. If the sources don't contain relevant information, say so honestly.
NEVER make up quotes or attribute statements to people unless those exact quotes appear in the sources. Do not fabricate attributions."""
        
        system_prompt = f"User context: {user_context}\n\n{base_prompt}" if user_context else base_prompt

        prompt = f"""Sources:
{context}

Q: {question}

Answer {"thoroughly" if deep_think else "concisely"} with inline [N] citations (NO references list at end):"""

        step_start = time.time()
        full_answer = ""
        use_fast = not deep_think
        buffer = ""
        references_started = False
        
        async for token in self._stream_ollama(system_prompt, prompt, deep_think=deep_think, use_fast_model=use_fast):
            buffer += token
            full_answer += token
            
            # Check if we've hit a References section - stop streaming if so
            if not references_started:
                lower_buffer = buffer.lower()
                # Look for reference section headers (must be on their own line)
                for marker in ["\nreferences:", "\nreferences\n", "\nsources:\n", "\ncitations:\n", "\n\n[1] "]:
                    if marker in lower_buffer:
                        references_started = True
                        print(f"[RAG STREAM] Detected references section, stopping output")
                        break
            
            if not references_started:
                yield {"type": "token", "content": token}
        
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

    async def _generate_quick_summary(self, question: str, context: str, num_citations: int) -> str:
        """Generate a quick 2-3 sentence summary using fast model (phi4-mini)"""
        try:
            system_prompt = f"""You are a helpful assistant. Provide a brief 2-3 sentence summary answering the question.
Use inline citations like [1], [2] etc. to reference the sources. Be direct and concise.
IMPORTANT: Do NOT add a References section at the end. Only use inline [N] citations within the text.
NEVER make up quotes or attribute statements to people unless those exact quotes appear in the sources."""
            
            # Use truncated context for speed
            truncated_context = context[:2000] if len(context) > 2000 else context
            
            prompt = f"""Sources:
{truncated_context}

Question: {question}

Brief summary (2-3 sentences with inline [N] citations only, NO references list):"""
            
            # Use fast model for quick summary
            response = await self._call_ollama(system_prompt, prompt, model=settings.ollama_fast_model)
            return response.strip()
        except Exception as e:
            print(f"Failed to generate quick summary: {e}")
            return ""

    async def _generate_follow_up_questions_fast(self, question: str, context: str) -> List[str]:
        """Generate follow-up questions using fast model"""
        try:
            system_prompt = "Generate exactly 3 follow-up questions. Output ONLY the questions, one per line. No preamble."
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
        """Generate suggested questions for a notebook"""
        # Placeholder implementation
        return [
            "What are the main topics covered?",
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
        
        # Build system prompt with user context, memory, and mode
        base_prompt = f"""Answer using sources [1]-[{num_citations}]. Be concise (1-2 paragraphs). Inline citations only. No reference list at end.
IMPORTANT: Only answer based on the provided sources. If the sources don't contain relevant information, say so honestly.
NEVER make up quotes or attribute statements to people unless those exact quotes appear in the sources. Do not fabricate attributions."""
        
        # Add Deep Think chain-of-thought instructions
        if deep_think:
            base_prompt = f"""You are in Deep Think mode. Think through this problem step by step:
1. First, identify the key aspects of the question
2. Consider relevant information from the sources
3. Reason through the implications
4. Synthesize your findings into a clear, well-reasoned answer

Answer using sources [1]-[{num_citations}]. Use inline citations. Be thorough and analytical.
IMPORTANT: Only use information from the provided sources. NEVER make up quotes or attribute statements to people unless those exact quotes appear in the sources."""
        
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
        use_model = model or settings.ollama_model
        async with httpx.AsyncClient(timeout=timeout) as client:
            print(f"Calling Ollama with model: {use_model}")
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": use_model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": False
                }
            )
            result = response.json()
            print(f"Ollama response received, length: {len(result.get('response', ''))}")
            return result.get("response", "No response from LLM")

    async def _stream_ollama(self, system_prompt: str, prompt: str, deep_think: bool = False, use_fast_model: bool = False) -> AsyncGenerator[str, None]:
        """Stream response from Ollama API with stop sequences to prevent citation lists
        
        Args:
            deep_think: Use CoT prompting with lower temperature (System 2 deliberate)
            use_fast_model: Use fast model (llama3.2:3b) instead of main model (olmo-3:7b-think)
        """
        timeout = httpx.Timeout(10.0, read=600.0)
        
        # Select model based on mode
        # Fast mode (bunny): use llama3.2:3b for quick conversational responses
        # Think mode (brain): use olmo-3:7b-think for thorough analysis (64K context, strong reasoning)
        if use_fast_model and not deep_think:
            model = settings.ollama_fast_model
            temperature = 0.7
            top_p = 0.9
        elif deep_think:
            model = settings.ollama_model
            # OLMo-3-Think recommended settings: temp=0.6, top_p=0.95
            temperature = 0.6
            top_p = 0.95
        else:
            model = settings.ollama_model
            temperature = 0.6
            top_p = 0.95
        
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
            async with client.stream(
                "POST",
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": True,
                    "stop": stop_sequences,
                    "options": {
                        "temperature": temperature,
                        "top_p": top_p
                    }
                }
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        import json
                        data = json.loads(line)
                        if "response" in data:
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

    def _chunk_text(self, text: str) -> List[str]:
        """Chunk text into smaller pieces"""
        chunk_size = settings.chunk_size
        chunk_overlap = settings.chunk_overlap

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - chunk_overlap

        return chunks

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
rag_service = RAGEngine()
