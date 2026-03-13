"""
RAG Generation — Prompt building, output cleaning, answer generation, and
suggested/follow-up question generation.

Extracted from rag_engine.py. RAGEngine delegates to these for prompt
construction, LLM answer generation, and output post-processing.
"""
import re
from typing import Dict, List, Optional

from config import settings
from services import rag_llm


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
FORMAT: Use markdown for structure — bold key terms, use headers (##) to separate major sections, and bullet points for lists of items. Provide a thorough analysis (3-5 paragraphs or equivalent structured content). Show your reasoning."""

    else:  # synthesis
        return f"""You are synthesizing information from multiple sources into a comprehensive answer.

APPROACH:
- Open with a clear, direct definition or answer to the core question
- Cover each major aspect the user asked about with substantive detail
- Weave together insights from different sources
- Note agreements and any tensions between sources
- Use specific examples and concrete details from the sources

{output_rules}
FORMAT: Use markdown for readability — bold key terms, use headers (##) to separate major topics, and bullet points or numbered lists when covering multiple items. Aim for a thorough answer (2-4 paragraphs or equivalent structured content). Do NOT be brief — give the user a complete picture."""


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


# ─── Answer Generation ──────────────────────────────────────────────────────────

async def generate_answer(
    question: str,
    context: str,
    num_citations: int = 5,
    llm_provider: Optional[str] = None,
    notebook_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    deep_think: bool = False,
    detect_response_format_fn=None,
) -> Dict:
    """Generate answer using LLM with memory augmentation and user personalization.
    
    Returns dict with answer, memory_used, and memory_context_summary.
    """
    from services.memory_agent import memory_agent

    # If no citations/context, refuse to answer to prevent hallucination
    if num_citations == 0 or not context.strip():
        return {
            "answer": "I don't have enough relevant information in your documents to answer this question accurately. Try uploading more documents related to this topic, or rephrase your question.",
            "memory_used": [],
            "memory_context_summary": None
        }

    memory_used = []

    # Get user profile for personalization
    from api.settings import get_user_profile_sync, build_user_context
    user_profile = get_user_profile_sync()
    user_context = build_user_context(user_profile)

    # Get memory context with dynamic budget based on existing context size
    # Estimate tokens already consumed by RAG context + question
    estimated_context_tokens = memory_agent.count_tokens(context) + memory_agent.count_tokens(question)
    memory_context = await memory_agent.get_memory_context(
        query=question,
        notebook_id=notebook_id,
        max_tokens=500,
        conversation_token_count=estimated_context_tokens
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

    # Zero-latency format hint (list, code, table, steps, or empty)
    if detect_response_format_fn:
        format_hint = detect_response_format_fn(question)
        if format_hint:
            system_parts.append(format_hint)

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
    if provider == "openai":
        answer = await rag_llm.call_openai(system_prompt, prompt)
    elif provider == "anthropic":
        answer = await rag_llm.call_anthropic(system_prompt, prompt)
    else:
        answer = await rag_llm.call_ollama(system_prompt, prompt)

    return {
        "answer": answer,
        "memory_used": memory_used,
        "memory_context_summary": memory_context.core_memory_block[:200] if memory_context.core_memory_block else None
    }


# ─── Follow-Up & Suggested Questions ────────────────────────────────────────────

async def generate_follow_up_questions_fast(question: str, context: str, answer: str = "") -> List[str]:
    """Generate contextual follow-up questions using fast model."""
    try:
        system_prompt = """Generate exactly 3 follow-up questions that would help the user explore this topic deeper.
Questions should:
- Build on what was just answered
- Explore related aspects not yet covered
- Be specific and actionable
Output ONLY the questions, one per line. No numbering, no preamble."""
        prompt = f"Topic: {question}\n\nContext: {context[:1000]}\n\n3 questions:"

        response = await rag_llm.call_ollama(system_prompt, prompt, model=settings.ollama_fast_model)

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


async def generate_proactive_insights(notebook_id: str, limit: int = 3) -> List[Dict]:
    """Generate proactive insights from document content."""
    try:
        from services import rag_storage

        table = rag_storage.get_table(notebook_id)

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

        response = await rag_llm.call_ollama(
            "You are a helpful analyst. Generate brief, specific insights.",
            prompt,
            model=settings.ollama_fast_model
        )

        insights = []
        for line in response.strip().split('\n'):
            line = line.strip()
            if line and ('\U0001f4a1' in line or line.startswith('-')):
                insight = line.replace('\U0001f4a1', '').strip().lstrip('-').strip()
                if insight and len(insight) > 10:
                    insights.append({
                        "text": insight,
                        "type": "proactive"
                    })

        return insights[:limit]

    except Exception as e:
        print(f"[RAG] Proactive insights generation failed: {e}")
        return []


async def get_suggested_questions(notebook_id: str) -> List[str]:
    """Generate suggested questions based on actual document content."""
    try:
        from services import rag_storage

        table = rag_storage.get_table(notebook_id)

        # Try to get document summaries first (chunk_index = -1)
        try:
            all_rows = table.search([0.0] * settings.embedding_dim).limit(30).to_list()
            summaries = [r for r in all_rows if r.get('chunk_index') == -1]
            regular_chunks = [r for r in all_rows if r.get('chunk_index') != -1][:5]
        except Exception:
            return default_suggested_questions()

        if not summaries and not regular_chunks:
            return default_suggested_questions()

        # Build context from summaries or sample chunks
        if summaries:
            context = "\n\n".join([s.get('text', '')[:400] for s in summaries[:3]])
        else:
            context = "\n\n".join([c.get('text', '')[:300] for c in regular_chunks[:3]])

        prompt = f"""Based on these document excerpts, generate 3 specific questions a user might want to ask.

Documents:
{context}

Generate exactly 3 questions, one per line. Questions should be specific to the content, not generic.
No numbering, no preamble, just the questions."""

        response = await rag_llm.call_ollama(
            "Generate 3 specific questions based on document content. Output only questions, one per line.",
            prompt,
            model=settings.ollama_fast_model
        )

        # Parse questions from response
        questions = []
        for line in response.strip().split('\n'):
            line = line.strip()
            if line and not line[0].isdigit() and '?' in line:
                question = line.lstrip('- \u2022').strip()
                if question and len(question) > 10:
                    questions.append(question)

        return questions[:3] if questions else default_suggested_questions()

    except Exception as e:
        print(f"[RAG] Suggested questions generation failed: {e}")
        return default_suggested_questions()
