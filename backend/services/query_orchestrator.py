"""Query Orchestrator for v0.60 Agentic RAG

Handles complex multi-step queries by:
1. Classifying query complexity
2. Decomposing complex queries into sub-queries
3. Executing sub-queries (parallel where possible)
4. Synthesizing final answer from sub-results
"""
import asyncio
import re
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

import httpx
from config import settings


@dataclass
class SubQuery:
    """A decomposed sub-query with metadata."""
    query: str
    purpose: str  # What this sub-query is trying to find
    depends_on: List[int] = None  # Indices of sub-queries this depends on
    
    def __post_init__(self):
        if self.depends_on is None:
            self.depends_on = []


@dataclass 
class SubQueryResult:
    """Result from executing a sub-query."""
    query: str
    answer: str
    citations: List[Dict]
    confidence: float


class QueryOrchestrator:
    """Orchestrates complex multi-step queries.
    
    For simple queries: passes directly to RAG engine (fast path)
    For complex queries: decomposes, executes sub-queries, synthesizes
    """
    
    def __init__(self, rag_engine):
        self.rag_engine = rag_engine
        self.ollama_base_url = settings.ollama_base_url
        self.fast_model = settings.ollama_fast_model
        self.main_model = settings.ollama_model
    
    def classify_complexity(self, query: str) -> str:
        """Classify query complexity level.
        
        Returns: 'simple', 'moderate', or 'complex'
        """
        q_lower = query.lower()
        
        # COMPLEX indicators: multiple documents, comparison, synthesis, long queries
        complex_patterns = [
            r'compare.*(?:to|with|against)',
            r'(?:write|create|draft).*(?:review|summary|report)',
            r'(?:analyze|evaluate|assess).*(?:performance|progress|metrics)',
            r'based on.*(?:all|multiple|different)',
            r'looking at.*(?:last|past|recent).*(?:months?|quarters?|years?)',
            r'(?:pros?\s+(?:and|&)\s+cons?|advantages?\s+(?:and|&)\s+disadvantages?)',
            r'step.by.step',
            r'(?:how|why).*(?:and|also|additionally)',
        ]
        
        for pattern in complex_patterns:
            if re.search(pattern, q_lower):
                return 'complex'
        
        # Multiple questions = complex
        if query.count('?') > 1:
            return 'complex'
        
        # Very long queries are likely complex
        if len(query) > 200:
            return 'complex'
        
        # MODERATE indicators: multiple entities or time periods
        moderate_patterns = [
            r'(?:q[1-4]|quarter).*(?:and|to|through).*(?:q[1-4]|quarter)',
            r'(?:both|all|each).*(?:quarters?|months?|years?)',
            r'(?:chris|christopher).*(?:and|vs\.?|versus)',
            r'(?:compare|difference|between)',
        ]
        
        for pattern in moderate_patterns:
            if re.search(pattern, q_lower):
                return 'moderate'
        
        # Default to simple
        return 'simple'
    
    async def decompose_query(self, query: str) -> List[SubQuery]:
        """Decompose a complex query into sub-queries using LLM.
        
        Uses the fast model for quick decomposition.
        """
        prompt = f"""Break down this complex question into simpler sub-questions that can be answered independently.

QUESTION: {query}

OUTPUT FORMAT (one per line):
1. [sub-question] | [purpose]
2. [sub-question] | [purpose]
...

RULES:
- Each sub-question should be answerable from a single document
- Include questions to gather all needed facts
- End with a synthesis question if needed
- Maximum 5 sub-questions

SUB-QUESTIONS:"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_base_url}/api/generate",
                    json={
                        "model": self.fast_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": 500}
                    }
                )
                
                if response.status_code == 200:
                    result = response.json().get("response", "")
                    return self._parse_sub_queries(result)
        except Exception as e:
            print(f"[Orchestrator] Decomposition failed: {e}")
        
        # Fallback: return original query as single sub-query
        return [SubQuery(query=query, purpose="Answer the full question")]
    
    def _parse_sub_queries(self, llm_output: str) -> List[SubQuery]:
        """Parse LLM output into SubQuery objects."""
        sub_queries = []
        
        for line in llm_output.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Remove numbering (1., 2., etc.)
            line = re.sub(r'^\d+\.\s*', '', line)
            
            # Split by | to get query and purpose
            if '|' in line:
                parts = line.split('|', 1)
                query = parts[0].strip()
                purpose = parts[1].strip() if len(parts) > 1 else "Find relevant information"
            else:
                query = line
                purpose = "Find relevant information"
            
            if query and len(query) > 10:
                sub_queries.append(SubQuery(query=query, purpose=purpose))
        
        # Limit to 5 sub-queries
        return sub_queries[:5] if sub_queries else [SubQuery(query=llm_output, purpose="Answer question")]
    
    async def execute_sub_queries(
        self, 
        sub_queries: List[SubQuery], 
        notebook_id: str,
        conversation_id: Optional[str] = None
    ) -> List[SubQueryResult]:
        """Execute sub-queries, parallelizing independent ones."""
        results = []
        
        # For now, execute all in parallel (no dependency tracking yet)
        # Future: respect depends_on for sequential execution
        tasks = []
        for sq in sub_queries:
            task = self._execute_single_query(sq, notebook_id, conversation_id)
            tasks.append(task)
        
        # Execute in parallel
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for sq, result in zip(sub_queries, raw_results):
            if isinstance(result, Exception):
                print(f"[Orchestrator] Sub-query failed: {sq.query[:50]}... - {result}")
                results.append(SubQueryResult(
                    query=sq.query,
                    answer="Could not retrieve information.",
                    citations=[],
                    confidence=0.0
                ))
            else:
                results.append(result)
        
        return results
    
    async def _execute_single_query(
        self, 
        sub_query: SubQuery, 
        notebook_id: str,
        conversation_id: Optional[str]
    ) -> SubQueryResult:
        """Execute a single sub-query against the RAG engine."""
        try:
            result = await self.rag_engine.query(
                notebook_id=notebook_id,
                question=sub_query.query,
                conversation_id=conversation_id,
                llm_provider="ollama"
            )
            
            return SubQueryResult(
                query=sub_query.query,
                answer=result.get("answer", ""),
                citations=result.get("citations", []),
                confidence=result.get("confidence", 0.5)
            )
        except Exception as e:
            print(f"[Orchestrator] Query execution error: {e}")
            return SubQueryResult(
                query=sub_query.query,
                answer="Error retrieving information.",
                citations=[],
                confidence=0.0
            )
    
    async def synthesize_answer(
        self, 
        original_query: str, 
        sub_results: List[SubQueryResult]
    ) -> Dict:
        """Synthesize final answer from sub-query results."""
        # Build context from sub-results
        context_parts = []
        all_citations = []
        citation_map = {}  # Map old citation numbers to new ones
        
        for i, result in enumerate(sub_results):
            if result.answer and result.confidence > 0.2:
                context_parts.append(f"Finding {i+1}: {result.answer}")
                
                # Collect and renumber citations
                for citation in result.citations:
                    new_num = len(all_citations) + 1
                    old_num = citation.get("number", new_num)
                    citation_map[f"[{old_num}]"] = f"[{new_num}]"
                    citation["number"] = new_num
                    all_citations.append(citation)
        
        if not context_parts:
            return {
                "answer": "I couldn't find enough information to answer this question.",
                "citations": [],
                "sources": [],
                "follow_up_questions": [],
                "low_confidence": True,
                "sub_queries": [sq.query for sq in sub_results]
            }
        
        context = "\n\n".join(context_parts)
        
        # Generate synthesized answer
        prompt = f"""Based on these findings, answer the original question.

ORIGINAL QUESTION: {original_query}

FINDINGS:
{context}

Provide a comprehensive answer that synthesizes all the findings. Use [1], [2], etc. to cite sources."""

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.ollama_base_url}/api/generate",
                    json={
                        "model": self.main_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": 1000}
                    }
                )
                
                if response.status_code == 200:
                    answer = response.json().get("response", "")
                    
                    # Extract unique sources from citations
                    sources = list(set(c.get("source_id", "") for c in all_citations if c.get("source_id")))
                    
                    return {
                        "answer": answer.strip(),
                        "citations": all_citations,
                        "sources": sources,
                        "follow_up_questions": [],
                        "low_confidence": len(all_citations) < 2,
                        "sub_queries": [r.query for r in sub_results],
                        "orchestrated": True
                    }
        except Exception as e:
            print(f"[Orchestrator] Synthesis failed: {e}")
        
        # Fallback: concatenate findings
        sources = list(set(c.get("source_id", "") for c in all_citations if c.get("source_id")))
        return {
            "answer": context,
            "citations": all_citations,
            "sources": sources,
            "follow_up_questions": [],
            "low_confidence": len(all_citations) < 2,
            "sub_queries": [r.query for r in sub_results],
            "orchestrated": True
        }
    
    async def process(
        self, 
        query: str, 
        notebook_id: str,
        conversation_id: Optional[str] = None,
        llm_provider: str = "ollama"
    ) -> Dict:
        """Main entry point: process a query with appropriate complexity handling.
        
        Returns dict with: answer, citations, complexity, orchestrated, etc.
        """
        start_time = time.time()
        
        # Step 1: Classify complexity
        complexity = self.classify_complexity(query)
        print(f"[Orchestrator] Query complexity: {complexity}")
        
        # Step 2: Route based on complexity
        if complexity == 'simple':
            # Fast path - direct to RAG engine
            result = await self.rag_engine.query(
                notebook_id=notebook_id,
                question=query,
                conversation_id=conversation_id,
                llm_provider=llm_provider
            )
            result["complexity"] = complexity
            result["orchestrated"] = False
            result["processing_time"] = time.time() - start_time
            return result
        
        elif complexity == 'moderate':
            # Moderate path - query expansion but no full decomposition
            # For now, treat same as simple (can enhance later)
            result = await self.rag_engine.query(
                notebook_id=notebook_id,
                question=query,
                conversation_id=conversation_id,
                llm_provider=llm_provider
            )
            result["complexity"] = complexity
            result["orchestrated"] = False
            result["processing_time"] = time.time() - start_time
            return result
        
        else:  # complex
            # Full orchestration path
            print(f"[Orchestrator] Decomposing complex query...")
            
            # Decompose
            sub_queries = await self.decompose_query(query)
            print(f"[Orchestrator] Decomposed into {len(sub_queries)} sub-queries")
            for i, sq in enumerate(sub_queries):
                print(f"  {i+1}. {sq.query[:60]}...")
            
            # Execute
            sub_results = await self.execute_sub_queries(
                sub_queries, notebook_id, conversation_id
            )
            
            # Synthesize
            result = await self.synthesize_answer(query, sub_results)
            result["complexity"] = complexity
            result["processing_time"] = time.time() - start_time
            
            return result


# Singleton instance (initialized lazily with rag_engine)
_orchestrator_instance = None

def get_orchestrator(rag_engine) -> QueryOrchestrator:
    """Get or create the query orchestrator singleton."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = QueryOrchestrator(rag_engine)
    return _orchestrator_instance
