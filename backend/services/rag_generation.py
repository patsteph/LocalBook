"""
RAG Generation — Prompt building, output cleaning, and answer quality utilities.

Extracted from rag_engine.py Phase 4a. Pure functions with no instance state.
RAGEngine delegates to these for prompt construction and output post-processing.
"""
import re
from typing import List


# ─── Prompt Templates ────────────────────────────────────────────────────────────

def get_prompt_for_query_type(query_type: str, num_citations: int, avg_confidence: float = 0.5) -> str:
    """Get optimized prompt based on query classification.
    
    v1.1.0: Enhanced with query-type-specific prompts per PROMPT_AUDIT.md Phase 0.2
    - Factual: Exact value extraction with verification
    - Complex: Step-by-step reasoning with synthesis
    - Synthesis: Multi-source integration
    """
    output_rules = """OUTPUT RULES:
1. Write ONLY your answer - no preamble, no "References:" section
2. Cite sources inline as [1], [2] after facts
3. If info not in sources, say "I couldn't find this in the documents."
"""

    if query_type == 'factual':
        return f"""You are extracting specific facts from source documents.

CRITICAL - EXACT VALUE EXTRACTION:
- Find the EXACT values in the sources. Do not estimate or round.
- For numbers: Quote the exact figure from the source
- For counts: Count the actual items listed in the source
- For dates: Use the exact date format from the source
- For names: Use the exact spelling from the source

VERIFICATION: Before answering, locate the exact text in the source that contains your answer.

{output_rules}
Answer in 1-2 sentences with the specific fact.

EXAMPLE:
Question: "How many demos did Chris do in Q1?"
GOOD: "Chris conducted 7 demos in Q1 2026 [1]."
BAD: "Chris did several demos..." (vague)
BAD: "Chris conducted approximately 7 demos..." (hedging)"""

    elif query_type == 'complex':
        return f"""You are analyzing a complex question that requires reasoning across sources.

APPROACH:
1. Identify the key aspects of the question
2. Find relevant evidence in each source
3. Synthesize findings into a coherent analysis
4. Draw a clear conclusion

{output_rules}
Provide a thorough analysis in 2-3 paragraphs. Show your reasoning."""

    else:  # synthesis
        return f"""You are synthesizing information from multiple sources.

APPROACH:
- Weave together insights from different sources
- Note agreements and any tensions between sources
- Prioritize the most important points

{output_rules}
Provide a clear, integrated answer in 1-2 paragraphs."""


# ─── Output Cleaning ─────────────────────────────────────────────────────────────

def clean_llm_output(text: str) -> str:
    """Clean up LLM output artifacts.
    
    Minimal post-processing - let the prompt do the heavy lifting.
    Only clean up formatting artifacts that slip through.
    """
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
    
    # Clean incomplete citation brackets at end
    text = re.sub(r'\s*\[\d+\]\s*\[\d+\.?\s*$', '', text)
    text = re.sub(r'\s*\[\d[\d,\s]*\.?\s*$', '', text)
    
    # Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    return text


# ─── Default Fallbacks ───────────────────────────────────────────────────────────

def default_suggested_questions() -> List[str]:
    """Fallback suggested questions when content-based generation fails."""
    return [
        "What are the main topics covered in my documents?",
        "Can you summarize the key points?",
        "What are the most important findings?"
    ]
