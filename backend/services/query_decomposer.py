"""Query Decomposition Service

Breaks complex questions into simpler sub-questions for better retrieval.
Uses LLM to intelligently decompose multi-part or comparison questions.
"""
import json
import re
from typing import Dict, List, Tuple
import httpx

from config import settings


class QueryDecomposer:
    """Decomposes complex queries into simpler sub-queries for better retrieval."""
    
    def __init__(self):
        self.complexity_threshold = 2  # Minimum sub-questions to trigger decomposition
        self.max_sub_queries = 4  # Maximum sub-queries to avoid explosion
    
    def is_complex_query(self, question: str) -> Tuple[bool, str]:
        """Determine if a query should be decomposed.
        
        Returns: (is_complex, complexity_type)
        """
        q_lower = question.lower()
        
        # Multi-part questions (contains "and" joining distinct concepts)
        if re.search(r'\b(and|as well as|along with|plus)\b.*\?', q_lower):
            # Check if it's actually joining two distinct questions
            if '?' in question[:-1] or re.search(r'\b(what|how|who|when|where|why)\b.*\b(what|how|who|when|where|why)\b', q_lower):
                return True, "multi_part"
        
        # Comparison questions
        comparison_patterns = [
            r'\b(compare|contrast|difference|similarities|versus|vs\.?)\b',
            r'\b(better|worse|more|less|higher|lower)\s+than\b',
            r'\b(between .+ and)\b',
        ]
        for pattern in comparison_patterns:
            if re.search(pattern, q_lower):
                return True, "comparison"
        
        # Multi-entity questions
        if re.search(r'\b(each|all|every|both)\b.*\b(and|,)\b', q_lower):
            return True, "multi_entity"
        
        # Questions with multiple time periods
        time_patterns = re.findall(r'\b(q[1-4]|2\d{3}|january|february|march|april|may|june|july|august|september|october|november|december)\b', q_lower)
        if len(time_patterns) >= 2:
            return True, "multi_temporal"
        
        # Long questions (>100 chars) with multiple clauses
        if len(question) > 100 and question.count(',') >= 2:
            return True, "complex_structure"
        
        return False, "simple"
    
    async def decompose(self, question: str) -> List[str]:
        """Decompose a complex question into simpler sub-questions.
        
        Returns list of sub-questions (includes original if decomposition fails).
        """
        is_complex, complexity_type = self.is_complex_query(question)
        
        if not is_complex:
            return [question]
        
        print(f"[QueryDecomposer] Decomposing {complexity_type} query")
        
        try:
            sub_questions = await self._decompose_with_llm(question, complexity_type)
            
            if sub_questions and len(sub_questions) >= self.complexity_threshold:
                print(f"[QueryDecomposer] Decomposed into {len(sub_questions)} sub-questions")
                return sub_questions[:self.max_sub_queries]
            else:
                return [question]
                
        except Exception as e:
            print(f"[QueryDecomposer] Decomposition failed: {e}")
            return [question]
    
    async def _decompose_with_llm(self, question: str, complexity_type: str) -> List[str]:
        """Use LLM to decompose the question."""
        
        type_instructions = {
            "multi_part": "Break this into separate questions, one for each distinct part.",
            "comparison": "Create separate questions to gather info about each item being compared, then one for the comparison itself.",
            "multi_entity": "Create a question for each entity mentioned.",
            "multi_temporal": "Create a question for each time period mentioned.",
            "complex_structure": "Simplify into focused sub-questions that together answer the original."
        }
        
        prompt = f"""Break this complex question into simpler sub-questions that together will answer the original.

Question: {question}

Instructions: {type_instructions.get(complexity_type, "Break into simpler questions.")}

Rules:
1. Each sub-question should be self-contained and answerable independently
2. Together, the sub-questions should cover all aspects of the original
3. Keep sub-questions focused and specific
4. Return 2-4 sub-questions

Output as a JSON array of strings. Example:
["What is X?", "What is Y?", "How do X and Y compare?"]

JSON array:"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "temperature": 0.3}
                    }
                )
                
                if response.status_code != 200:
                    return []
                
                result = response.json().get("response", "")
                
                # Extract JSON array
                match = re.search(r'\[.*?\]', result, re.DOTALL)
                if match:
                    sub_questions = json.loads(match.group())
                    if isinstance(sub_questions, list) and all(isinstance(q, str) for q in sub_questions):
                        return sub_questions
                
                return []
                
        except Exception as e:
            print(f"[QueryDecomposer] LLM error: {e}")
            return []
    
    async def search_and_merge(
        self,
        question: str,
        search_fn,
        top_k: int = 4
    ) -> Tuple[List[Dict], bool]:
        """Decompose query, search for each sub-query, and merge results.
        
        Args:
            question: Original question
            search_fn: Async function(query, top_k) -> List[Dict] for searching
            top_k: Results per sub-query
            
        Returns: (merged_results, was_decomposed)
        """
        sub_questions = await self.decompose(question)
        
        if len(sub_questions) <= 1:
            # Not decomposed, return regular search
            results = await search_fn(question, top_k)
            return results, False
        
        # Search for each sub-question
        all_results = []
        seen_ids = set()
        
        # Reduce per-query top_k to avoid explosion
        per_query_k = max(2, top_k // len(sub_questions))
        
        for sub_q in sub_questions:
            results = await search_fn(sub_q, per_query_k)
            
            for r in results:
                # Deduplicate by chunk ID or text hash
                r_id = r.get('chunk_id', hash(r.get('text', '')[:100]))
                if r_id not in seen_ids:
                    all_results.append(r)
                    seen_ids.add(r_id)
        
        print(f"[QueryDecomposer] Merged {len(all_results)} unique results from {len(sub_questions)} sub-queries")
        
        return all_results, True
    
    def build_decomposed_prompt(
        self,
        original_question: str,
        sub_questions: List[str],
        sub_answers: List[str]
    ) -> str:
        """Build a prompt to synthesize answers to sub-questions.
        
        Used when generating final answer from decomposed query results.
        """
        parts = []
        for i, (q, a) in enumerate(zip(sub_questions, sub_answers)):
            parts.append(f"Sub-question {i+1}: {q}\nAnswer: {a}")
        
        return f"""Original question: {original_question}

I've gathered information for the following sub-questions:

{chr(10).join(parts)}

Now synthesize a comprehensive answer to the original question using the information above.
Be sure to address all aspects of the original question.

Answer:"""


# Singleton instance
query_decomposer = QueryDecomposer()
