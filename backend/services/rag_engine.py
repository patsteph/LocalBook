"""RAG (Retrieval Augmented Generation) Engine"""
import lancedb
import uuid
from pathlib import Path
from typing import List, Dict, Optional, AsyncGenerator
from sentence_transformers import SentenceTransformer
from config import settings
from storage.source_store import source_store
from services.memory_agent import memory_agent
from models.memory import MemoryExtractionRequest
from models.knowledge_graph import ConceptExtractionRequest
import httpx
import time
import asyncio

class RAGEngine:
    """RAG engine for document Q&A"""

    def __init__(self):
        self.db_path = settings.db_path
        self.embedding_model = None  # Lazy load
        self.db = None
    
    def _load_embedding_model(self):
        """Force load the embedding model (used for warmup)"""
        if self.embedding_model is None:
            self.embedding_model = SentenceTransformer(settings.embedding_model)
        return self.embedding_model
    
    def _get_embedding_model(self):
        """Lazy load embedding model"""
        if self.embedding_model is None:
            self.embedding_model = SentenceTransformer(settings.embedding_model)
        return self.embedding_model

    def _get_table(self, notebook_id: str):
        """Get or create LanceDB table for notebook"""
        if self.db is None:
            self.db = lancedb.connect(str(self.db_path))

        table_name = f"notebook_{notebook_id}"

        # Check if table exists
        if table_name not in self.db.table_names():
            # Create table with schema including useful metadata fields
            model = self._get_embedding_model()
            self.db.create_table(
                table_name,
                data=[{
                    "vector": model.encode("placeholder").tolist(),
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
        model = self._get_embedding_model()
        embeddings = model.encode(chunks)

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

        # Extract concepts for knowledge graph (background task - don't block upload)
        asyncio.create_task(self._extract_concepts_for_source(
            notebook_id=notebook_id,
            source_id=source_id,
            chunks=chunks
        ))

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
        """Extract concepts from document chunks for knowledge graph (runs in background)"""
        try:
            # Import here to avoid circular imports
            from services.knowledge_graph import knowledge_graph_service
            from api.constellation_ws import notify_concept_added, notify_build_progress, notify_build_complete
            
            print(f"[KG] Starting concept extraction for source {source_id} ({len(chunks)} chunks)")
            
            total_concepts = 0
            chunks_to_process = [i for i in range(len(chunks)) if i % 3 == 0]
            
            # Process every 3rd chunk to balance coverage vs speed
            for idx, i in enumerate(chunks_to_process):
                chunk = chunks[i]
                    
                request = ConceptExtractionRequest(
                    text=chunk,
                    source_id=source_id,
                    chunk_index=i,
                    notebook_id=notebook_id
                )
                
                result = await knowledge_graph_service.extract_concepts(request)
                if result.concepts:
                    total_concepts += len(result.concepts)
                    print(f"[KG] Chunk {i}: extracted {len(result.concepts)} concepts, {len(result.links)} links")
                    
                    # Broadcast each new concept via WebSocket
                    for concept in result.concepts:
                        await notify_concept_added({
                            "name": concept.name,
                            "notebook_id": notebook_id,
                            "source_id": source_id
                        })
                
                # Broadcast progress
                progress = (idx + 1) / len(chunks_to_process) * 100
                await notify_build_progress({
                    "source_id": source_id,
                    "progress": round(progress, 1),
                    "concepts_found": total_concepts
                })
            
            print(f"[KG] Concept extraction complete for source {source_id}: {total_concepts} concepts")
            await notify_build_complete()
            
        except Exception as e:
            print(f"[KG] Concept extraction error: {e}")

    async def query(
        self,
        notebook_id: str,
        question: str,
        source_ids: Optional[List[str]] = None,
        top_k: int = 4,  # Reduced from 5 for speed
        enable_web_search: bool = False,
        llm_provider: Optional[str] = None
    ) -> Dict:
        """Query the RAG system"""
        total_start = time.time()
        print(f"\n{'='*60}")
        print(f"[RAG] Starting query: '{question[:50]}...'")
        print(f"{'='*60}")

        # Step 1: Generate query embedding
        step_start = time.time()
        model = self._get_embedding_model()
        query_embedding = model.encode(question).tolist()
        print(f"[RAG] Step 1 - Embedding generation: {time.time() - step_start:.2f}s")

        # Step 2: Get/open vector database table
        step_start = time.time()
        table = self._get_table(notebook_id)
        print(f"[RAG] Step 2 - Open LanceDB table: {time.time() - step_start:.2f}s")
        
        # Check if table has any data
        try:
            row_count = table.count_rows()
            print(f"[RAG] Table has {row_count} rows")
            if row_count == 0:
                return {
                    "answer": "I don't have any documents to search yet. Please upload some documents first, or the documents may still be processing.",
                    "citations": [],
                    "sources": [],
                    "web_sources": None,
                    "follow_up_questions": [],
                    "low_confidence": True
                }
        except Exception:
            pass  # If count fails, try the search anyway
        
        # Step 3: Vector search
        step_start = time.time()
        try:
            results = table.search(query_embedding).limit(top_k).to_list()
            print(f"[RAG] Step 3 - Vector search ({len(results)} results): {time.time() - step_start:.2f}s")
        except Exception as e:
            print(f"Search error: {e}")
            return {
                "answer": "I encountered an error searching your documents. The documents may need to be re-indexed.",
                "citations": [],
                "sources": [],
                "web_sources": None,
                "follow_up_questions": [],
                "low_confidence": True
            }

        # Filter by source_ids if specified
        if source_ids:
            results = [r for r in results if r["source_id"] in source_ids]

        # Step 4: Build context and citations
        step_start = time.time()
        context_chunks = []
        citations = []
        sources = set()
        
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
            
            # LanceDB cosine distance is 0-2 range for normalized vectors
            distance = result.get("_distance", 1.0)
            # Convert to confidence: 0 dist = 100%, 1.0 dist = 50%, 2.0 dist = 0%
            confidence = max(0, min(1, 1 - (distance / 2)))
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

        # Only filter out truly irrelevant results (< 35% confidence = distance > 1.3)
        quality_citations = [c for c in all_citations if c["confidence"] >= 0.35]
        
        # If filtering removed everything, keep top 3 anyway
        if len(quality_citations) == 0 and len(all_citations) > 0:
            quality_citations = all_citations[:3]
        
        print(f"[RAG] Citations: {len(quality_citations)} used (from {len(all_citations)} found)")
        
        # Renumber citations after filtering
        for i, citation in enumerate(quality_citations):
            citation["number"] = i + 1
        
        # Build context only from quality citations
        for citation in quality_citations:
            context_chunks.append(citation["text"])
            sources.add(citation["source_id"])
        
        citations = quality_citations

        # Low confidence only if NO quality citations at all
        low_confidence = len(citations) == 0
        num_citations = len(citations)
        print(f"[RAG] Step 4 - Build context & citations: {time.time() - step_start:.2f}s")
        print(f"[RAG] Quality citations: {num_citations} (filtered from {len(all_citations)})")
        
        # Build context with explicit citation numbers
        numbered_context = []
        for i, citation in enumerate(citations):
            numbered_context.append(f"[{i+1}] {citation['text']}")
        context = "\n\n".join(numbered_context)
        print(f"[RAG] Context size: {len(context)} chars, {len(context.split())} words")

        # Step 5: Generate answer using LLM (with memory augmentation)
        step_start = time.time()
        conversation_id = str(uuid.uuid4())  # Generate conversation ID for memory tracking
        answer_result = await self._generate_answer(question, context, num_citations, llm_provider, notebook_id, conversation_id)
        answer = answer_result["answer"]
        memory_used = answer_result.get("memory_used", [])
        memory_context_summary = answer_result.get("memory_context_summary")
        llm_time = time.time() - step_start
        print(f"[RAG] Step 5 - LLM answer generation: {llm_time:.2f}s")
        if memory_used:
            print(f"[RAG] Memory used: {memory_used}")
        
        # Step 6: Extract memories from the conversation (async, non-blocking)
        step_start = time.time()
        try:
            # Extract from user question
            await memory_agent.extract_memories(MemoryExtractionRequest(
                message=question,
                role="user",
                conversation_id=conversation_id,
                notebook_id=notebook_id
            ))
            # Extract from assistant answer
            await memory_agent.extract_memories(MemoryExtractionRequest(
                message=answer,
                role="assistant",
                conversation_id=conversation_id,
                notebook_id=notebook_id,
                context=question  # Provide question as context
            ))
            print(f"[RAG] Step 6 - Memory extraction: {time.time() - step_start:.2f}s")
        except Exception as e:
            print(f"[RAG] Memory extraction failed (non-fatal): {e}")
        
        # Step 7: Generate follow-up questions
        step_start = time.time()
        follow_up_questions = await self._generate_follow_up_questions(question, answer, context)
        print(f"[RAG] Step 7 - Follow-up questions: {time.time() - step_start:.2f}s")

        total_time = time.time() - total_start
        print(f"{'='*60}")
        print(f"[RAG] TOTAL query time: {total_time:.2f}s")
        print(f"{'='*60}\n")

        return {
            "answer": answer,
            "citations": citations,
            "sources": list(sources),
            "web_sources": None,  # TODO: implement web search
            "follow_up_questions": follow_up_questions,
            "low_confidence": low_confidence,
            "memory_used": memory_used,
            "memory_context_summary": memory_context_summary
        }

    async def query_stream(
        self,
        notebook_id: str,
        question: str,
        source_ids: Optional[List[str]] = None,
        top_k: int = 4,  # Reduced from 5 for speed
        llm_provider: Optional[str] = None
    ) -> AsyncGenerator[Dict, None]:
        """Query the RAG system with streaming response"""
        total_start = time.time()
        print(f"\n{'='*60}")
        print(f"[RAG STREAM] Starting query: '{question[:50]}...'")
        print(f"{'='*60}")

        # Step 1: Generate query embedding
        step_start = time.time()
        model = self._get_embedding_model()
        query_embedding = model.encode(question).tolist()
        print(f"[RAG STREAM] Step 1 - Embedding: {time.time() - step_start:.2f}s")

        # Step 2: Get table and search
        step_start = time.time()
        table = self._get_table(notebook_id)
        
        try:
            row_count = table.count_rows()
            if row_count == 0:
                yield {"type": "error", "content": "No documents indexed yet."}
                return
        except Exception:
            pass

        try:
            results = table.search(query_embedding).limit(top_k).to_list()
            print(f"[RAG STREAM] Step 2 - Search ({len(results)} results): {time.time() - step_start:.2f}s")
        except Exception as e:
            yield {"type": "error", "content": f"Search error: {e}"}
            return

        # Filter by source_ids if specified
        if source_ids:
            results = [r for r in results if r["source_id"] in source_ids]

        # Step 3: Build context and citations
        step_start = time.time()
        context_chunks = []
        citations = []
        sources = set()
        
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
            
            # LanceDB cosine distance is 0-2 range for normalized vectors
            distance = result.get("_distance", 1.0)
            # Convert to confidence: 0 dist = 100%, 1.0 dist = 50%, 2.0 dist = 0%
            confidence = max(0, min(1, 1 - (distance / 2)))
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

        # Only filter out truly irrelevant results (< 35% confidence = distance > 1.3)
        quality_citations = [c for c in all_citations if c["confidence"] >= 0.35]
        
        # If filtering removed everything, keep top 3 anyway
        if len(quality_citations) == 0 and len(all_citations) > 0:
            quality_citations = all_citations[:3]
            print(f"[RAG STREAM] Low confidence fallback: using top 3 citations")
        
        print(f"[RAG STREAM] Citations: {len(quality_citations)} used (from {len(all_citations)} found)")
        
        # Renumber citations after filtering
        for i, citation in enumerate(quality_citations):
            citation["number"] = i + 1
        
        # Build context only from quality citations
        for citation in quality_citations:
            context_chunks.append(citation["text"])
            sources.add(citation["source_id"])
        
        citations = quality_citations
        
        # Low confidence only if NO quality citations at all
        low_confidence = len(citations) == 0
        # Build context with explicit citation numbers
        numbered_context = []
        # Build context - use full chunks for accuracy (speed comes from top_k=4)
        for i, citation in enumerate(citations):
            numbered_context.append(f"[{i+1}] {citation['text']}")
        context = "\n\n".join(numbered_context)
        
        num_citations = len(citations)
        print(f"[RAG STREAM] Context: {len(context)} chars, {num_citations} citations")

        # Send citations immediately so UI can show them
        yield {
            "type": "citations",
            "citations": citations,
            "sources": list(sources),
            "low_confidence": low_confidence
        }

        # Step 4: Generate quick summary with fast model (phi4-mini)
        step_start = time.time()
        quick_summary = await self._generate_quick_summary(question, context, num_citations)
        print(f"[RAG STREAM] Step 4 - Quick summary (phi4): {time.time() - step_start:.2f}s")
        
        # Send quick summary immediately
        yield {
            "type": "quick_summary",
            "content": quick_summary
        }

        # Step 5: Start follow-up generation in background (parallel with detailed answer)
        followup_task = asyncio.create_task(
            self._generate_follow_up_questions_fast(question, context)
        )

        # Step 6: Stream the detailed answer with main model (mistral-nemo)
        system_prompt = f"""Answer using sources [1]-[{num_citations}]. Be concise (1-2 paragraphs). Inline citations only. No reference list at end."""

        prompt = f"""Sources:
{context}

Q: {question}

Answer concisely with inline [N] citations:"""

        step_start = time.time()
        full_answer = ""
        async for token in self._stream_ollama(system_prompt, prompt):
            full_answer += token
            yield {"type": "token", "content": token}
        
        print(f"[RAG STREAM] Step 6 - LLM streaming complete: {time.time() - step_start:.2f}s")

        # Step 7: Wait for follow-up questions (should be done or nearly done)
        step_start = time.time()
        follow_up_questions = await followup_task
        print(f"[RAG STREAM] Step 7 - Follow-ups ready: {time.time() - step_start:.2f}s")

        # Send completion with follow-up questions
        yield {
            "type": "done",
            "follow_up_questions": follow_up_questions
        }

        # Step 8: Extract memories from the conversation (async, non-blocking)
        step_start = time.time()
        try:
            conversation_id = str(uuid.uuid4())
            # Extract from user question
            await memory_agent.extract_memories(MemoryExtractionRequest(
                message=question,
                role="user",
                conversation_id=conversation_id,
                notebook_id=notebook_id
            ))
            # Extract from assistant answer
            await memory_agent.extract_memories(MemoryExtractionRequest(
                message=full_answer,
                role="assistant",
                conversation_id=conversation_id,
                notebook_id=notebook_id,
                context=question
            ))
            print(f"[RAG STREAM] Step 8 - Memory extraction: {time.time() - step_start:.2f}s")
        except Exception as e:
            print(f"[RAG STREAM] Memory extraction failed (non-fatal): {e}")

        total_time = time.time() - total_start
        print(f"{'='*60}")
        print(f"[RAG STREAM] TOTAL time: {total_time:.2f}s")
        print(f"{'='*60}\n")

    async def _generate_quick_summary(self, question: str, context: str, num_citations: int) -> str:
        """Generate a quick 2-3 sentence summary using fast model (phi4-mini)"""
        try:
            system_prompt = f"""You are a helpful assistant. Provide a brief 2-3 sentence summary answering the question.
Use inline citations like [1], [2] etc. to reference the sources. Be direct and concise.
IMPORTANT: Do NOT add a References section at the end. Only use inline [N] citations within the text."""
            
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
        """Generate follow-up questions using fast model (phi4-mini)"""
        try:
            system_prompt = "Generate 3 brief follow-up questions based on the context. One question per line."
            prompt = f"Topic: {question}\n\nContext summary: {context[:1000]}\n\nQuestions:"
            
            # Use fast model for follow-up generation
            response = await self._call_ollama(system_prompt, prompt, model=settings.ollama_fast_model)
            
            questions = [q.strip() for q in response.strip().split('\n') if q.strip()]
            questions = [q.lstrip('0123456789.-) ') for q in questions]
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

    async def _generate_answer(self, question: str, context: str, num_citations: int = 5, llm_provider: Optional[str] = None, notebook_id: Optional[str] = None, conversation_id: Optional[str] = None) -> Dict:
        """Generate answer using LLM with memory augmentation. Returns dict with answer and memory_used info."""
        
        # Check if memory is enabled (frontend can disable via localStorage)
        memory_used = []
        
        # Get memory context to augment the prompt
        memory_context = await memory_agent.get_memory_context(
            query=question,
            notebook_id=notebook_id,
            max_tokens=500  # Reserve tokens for memory
        )
        
        # Build system prompt with memory
        base_prompt = f"""Answer using sources [1]-[{num_citations}]. Be concise (1-2 paragraphs). Inline citations only. No reference list at end."""
        
        if memory_context.core_memory_block:
            system_prompt = f"{memory_context.core_memory_block}\n\n{base_prompt}"
            # Track that core memory was used
            if memory_context.core_memory_block.strip():
                memory_used.append("core_context")
        else:
            system_prompt = base_prompt
        
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
    
    async def _generate_follow_up_questions(self, question: str, answer: str, context: str) -> List[str]:
        """Generate follow-up questions based on the conversation"""
        try:
            system_prompt = """Based on the question asked and answer provided, generate exactly 3 short follow-up questions 
that would help the user explore the topic further. Return ONLY the questions, one per line, no numbering or bullets."""

            prompt = f"""Original question: {question}

Answer provided: {answer[:500]}

Generate 3 follow-up questions:"""

            # Use ollama for follow-up generation (fast)
            response = await self._call_ollama(system_prompt, prompt)
            
            # Parse response into list of questions
            questions = [q.strip() for q in response.strip().split('\n') if q.strip()]
            # Clean up any numbering or bullets
            questions = [q.lstrip('0123456789.-) ') for q in questions]
            # Return first 3 valid questions
            return questions[:3] if questions else []
        except Exception as e:
            print(f"Failed to generate follow-up questions: {e}")
            return []

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

    async def _stream_ollama(self, system_prompt: str, prompt: str) -> AsyncGenerator[str, None]:
        """Stream response from Ollama API with stop sequences to prevent citation lists"""
        timeout = httpx.Timeout(10.0, read=600.0)
        
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
            print(f"Streaming from Ollama with model: {settings.ollama_model}")
            async with client.stream(
                "POST",
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": True,
                    "stop": stop_sequences  # Stop sequences at top level for Ollama
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

# Global instance
rag_service = RAGEngine()
