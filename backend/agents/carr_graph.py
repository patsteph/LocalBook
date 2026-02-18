"""LangGraph CaRR Verification Loop

Generate → citation_verifier → conditional re-generate if hallucination_risk high.
Max 1 retry to avoid infinite loops.

Used by the RAG streaming pipeline: instead of a single LLM call, this graph
generates an answer, verifies it against citations, and retries once with
explicit grounding instructions if the answer has high hallucination risk.
"""

import logging
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class CaRRState(TypedDict):
    """State flowing through the CaRR verification loop."""
    # Inputs
    system_prompt: str
    user_prompt: str
    citations: List[Dict[str, Any]]
    deep_think: bool
    use_fast_model: bool
    # Generated answer
    answer: str
    # Verification
    verification: Optional[Dict[str, Any]]
    hallucination_risk: str  # "low", "medium", "high"
    # Loop control
    retry_count: int
    max_retries: int
    # Final output
    final_answer: str
    final_verification: Optional[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Node: generate_answer
# ---------------------------------------------------------------------------

async def generate_answer_node(state: CaRRState) -> Dict:
    """Generate an answer using the LLM."""
    from services.rag_engine import rag_engine

    system_prompt = state["system_prompt"]
    user_prompt = state["user_prompt"]
    retry_count = state.get("retry_count", 0)

    # On retry, add explicit grounding instructions
    if retry_count > 0:
        prev_verification = state.get("verification", {})
        feedback = prev_verification.get("feedback", "")

        grounding_addendum = (
            "\n\nIMPORTANT: Your previous answer had unsupported claims. "
            "This time, ONLY make statements that are directly supported by the source material. "
            "If a fact cannot be verified from the provided sources, do not include it. "
            "Use [N] citations for every factual claim."
        )
        if feedback:
            grounding_addendum += f"\nVerification feedback: {feedback}"

        system_prompt = system_prompt + grounding_addendum
        logger.info(f"[CaRR] Retry {retry_count}: added grounding instructions")

    # Collect full answer (non-streaming for verification)
    full_answer = ""
    async for token in rag_engine._stream_ollama(
        system_prompt,
        user_prompt,
        deep_think=state.get("deep_think", False),
        use_fast_model=state.get("use_fast_model", False),
    ):
        full_answer += token

    return {"answer": full_answer}


# ---------------------------------------------------------------------------
# Node: verify_citations
# ---------------------------------------------------------------------------

async def verify_citations_node(state: CaRRState) -> Dict:
    """Verify the answer against citations using CaRR."""
    from services.citation_verifier import citation_verifier

    answer = state["answer"]
    citations = state.get("citations", [])

    try:
        result = citation_verifier.verify_answer(answer, citations)
        verification = {
            "score": result.overall_score,
            "hallucination_risk": result.hallucination_risk,
            "feedback": result.feedback,
            "fully_supported": result.fully_supported_count,
            "partially_supported": result.partially_supported_count,
            "unsupported": result.unsupported_count,
            "no_citation": result.no_citation_count,
        }
        logger.info(
            f"[CaRR] Verification: score={result.overall_score:.2f}, "
            f"risk={result.hallucination_risk}, "
            f"retry={state.get('retry_count', 0)}"
        )
        return {
            "verification": verification,
            "hallucination_risk": result.hallucination_risk,
        }
    except Exception as e:
        logger.error(f"[CaRR] Verification failed: {e}")
        return {
            "verification": None,
            "hallucination_risk": "low",  # Don't retry on verification failure
        }


# ---------------------------------------------------------------------------
# Node: finalize
# ---------------------------------------------------------------------------

async def finalize_node(state: CaRRState) -> Dict:
    """Set the final answer and verification result."""
    return {
        "final_answer": state["answer"],
        "final_verification": state.get("verification"),
    }


# ---------------------------------------------------------------------------
# Edge: should we retry?
# ---------------------------------------------------------------------------

def should_retry(state: CaRRState) -> str:
    """Decide whether to retry generation or finalize."""
    risk = state.get("hallucination_risk", "low")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)

    if risk == "high" and retry_count < max_retries:
        logger.info(f"[CaRR] High hallucination risk — retrying (attempt {retry_count + 1})")
        return "retry"
    return "finalize"


# ---------------------------------------------------------------------------
# Node: increment retry counter
# ---------------------------------------------------------------------------

async def increment_retry_node(state: CaRRState) -> Dict:
    return {"retry_count": state.get("retry_count", 0) + 1}


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_carr_graph() -> StateGraph:
    builder = StateGraph(CaRRState)

    builder.add_node("generate", generate_answer_node)
    builder.add_node("verify", verify_citations_node)
    builder.add_node("increment_retry", increment_retry_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "generate")
    builder.add_edge("generate", "verify")
    builder.add_conditional_edges("verify", should_retry, {
        "retry": "increment_retry",
        "finalize": "finalize",
    })
    builder.add_edge("increment_retry", "generate")
    builder.add_edge("finalize", END)

    return builder


_checkpointer = MemorySaver()
carr_graph = build_carr_graph().compile(checkpointer=_checkpointer)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def verified_generate(
    system_prompt: str,
    user_prompt: str,
    citations: List[Dict[str, Any]],
    deep_think: bool = False,
    use_fast_model: bool = False,
    max_retries: int = 1,
) -> Dict[str, Any]:
    """Run the CaRR verification loop and return the final answer + verification.

    Returns:
        Dict with 'answer', 'verification', 'retried' (bool).
    """
    import time

    thread_id = f"carr-{int(time.time() * 1000)}"
    config = {"configurable": {"thread_id": thread_id}}

    initial: CaRRState = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "citations": citations,
        "deep_think": deep_think,
        "use_fast_model": use_fast_model,
        "answer": "",
        "verification": None,
        "hallucination_risk": "low",
        "retry_count": 0,
        "max_retries": max_retries,
        "final_answer": "",
        "final_verification": None,
    }

    result = await carr_graph.ainvoke(initial, config=config)

    return {
        "answer": result.get("final_answer", ""),
        "verification": result.get("final_verification"),
        "retried": result.get("retry_count", 0) > 0,
    }
