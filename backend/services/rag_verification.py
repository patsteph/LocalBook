"""
RAG Verification — CaRR citation verification, quality gates, and retry logic.

Extracted from rag_engine.py Phase 6. Owns all answer verification:
- Citation verification via citation_verifier
- CaRR LangGraph retry orchestration
- Quality gate (prompt artifact detection + score comparison)
- Reference section stripping from retry answers

The orchestrator (query_stream) calls these functions and handles
the SSE yield events itself.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class VerificationOutcome:
    """Result of the full verify-and-maybe-retry pipeline."""
    verification_result: Any  # VerificationResult from citation_verifier
    carr_retried: bool
    replacement_answer: Optional[str]  # Non-None if CaRR produced a better answer


def verify_answer(full_answer: str, citations: List[Dict]) -> Any:
    """Run citation verification on the answer.
    
    Returns a VerificationResult (or None on failure).
    """
    try:
        from services.citation_verifier import citation_verifier
        result = citation_verifier.verify_answer(full_answer, citations)
        print(f"[RAG VERIFY] CaRR verification: score={result.overall_score:.2f}, risk={result.hallucination_risk}")
        return result
    except Exception as e:
        print(f"[RAG VERIFY] CaRR verification failed (non-fatal): {e}")
        return None


async def attempt_carr_retry(
    verification_result: Any,
    system_prompt: str,
    user_prompt: str,
    citations: List[Dict],
    deep_think: bool,
    use_fast_model: bool,
    original_answer: str = "",
) -> Tuple[bool, Optional[str], Optional[Any]]:
    """Attempt a CaRR retry if verification shows high hallucination risk.
    
    Returns: (should_replace, new_answer_or_none, updated_verification_or_none)
    """
    if verification_result is None:
        return False, None, None

    if verification_result.hallucination_risk != "high":
        return False, None, None

    try:
        from agents.carr_graph import verified_generate

        carr_result = await verified_generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            citations=citations,
            deep_think=deep_think,
            use_fast_model=use_fast_model,
            max_retries=1,
        )

        if not (carr_result.get("retried") and carr_result.get("answer")):
            return False, None, None

        new_answer = carr_result["answer"]
        retry_verif = carr_result.get("verification") or {}
        retry_score = retry_verif.get("score", 0)
        retry_risk = retry_verif.get("hallucination_risk", "high")
        original_score = verification_result.overall_score

        # Quality gate 1: reject retry if it contains prompt artifacts or didn't improve
        prompt_leaks = ["answer with [n] citations", "user context:"]
        has_leak = any(p in new_answer.lower() for p in prompt_leaks)
        improved = retry_score > original_score and retry_risk != "high"

        if has_leak or not improved:
            print(f"[RAG VERIFY] CaRR retry REJECTED: leak={has_leak}, "
                  f"score {original_score:.2f}→{retry_score:.2f}, risk={retry_risk}")
            return False, None, None

        # Quality gate 2: reject if replacement is too short (< 60% of original)
        # CaRR retries tend to produce stripped-down, overly conservative answers
        if original_answer and len(new_answer) < len(original_answer) * 0.6:
            print(f"[RAG VERIFY] CaRR retry REJECTED: too short "
                  f"({len(new_answer)} chars vs original {len(original_answer)} chars)")
            return False, None, None

        # Quality gate 3: reject if original had markdown formatting that replacement lost
        import re
        orig_has_headers = bool(re.search(r'^#{1,3}\s', original_answer, re.MULTILINE))
        orig_has_lists = bool(re.search(r'^[\-\*]\s', original_answer, re.MULTILINE))
        new_has_headers = bool(re.search(r'^#{1,3}\s', new_answer, re.MULTILINE))
        new_has_lists = bool(re.search(r'^[\-\*]\s', new_answer, re.MULTILINE))
        if (orig_has_headers and not new_has_headers) or (orig_has_lists and not new_has_lists):
            print(f"[RAG VERIFY] CaRR retry REJECTED: lost formatting "
                  f"(headers: {orig_has_headers}→{new_has_headers}, lists: {orig_has_lists}→{new_has_lists})")
            return False, None, None

        # Strip references section from retry answer
        new_answer = _strip_references(new_answer)

        # Build updated verification result
        carr_verif = carr_result.get("verification")
        updated_verif = None
        if carr_verif:
            updated_verif = type(verification_result)(
                overall_score=carr_verif.get("score", verification_result.overall_score),
                hallucination_risk=carr_verif.get("hallucination_risk", "medium"),
                feedback=carr_verif.get("feedback", ""),
                claims=[],
                fully_supported_count=carr_verif.get("fully_supported", 0),
                partially_supported_count=carr_verif.get("partially_supported", 0),
                unsupported_count=carr_verif.get("unsupported", 0),
                no_citation_count=carr_verif.get("no_citation", 0),
            )

        print(f"[RAG VERIFY] CaRR retry accepted: score {original_score:.2f}→{retry_score:.2f}")
        return True, new_answer, updated_verif

    except Exception as carr_err:
        print(f"[RAG VERIFY] CaRR retry failed (non-fatal): {carr_err}")
        return False, None, None


def build_verification_payload(
    verification_result: Any,
    carr_retried: bool,
) -> Optional[Dict]:
    """Build the verification dict for the 'done' SSE event."""
    if verification_result is None:
        return None
    return {
        "score": verification_result.overall_score,
        "hallucination_risk": verification_result.hallucination_risk,
        "feedback": verification_result.feedback,
        "retried": carr_retried,
    }


def _strip_references(text: str) -> str:
    """Strip trailing references/sources sections from an answer."""
    import re
    for marker in ["\nReferences:", "\nreferences\n", "\nSources:\n"]:
        idx = text.lower().find(marker.lower())
        if idx > 0:
            return text[:idx]
    # Strip trailing --- followed by short stub content (e.g. "---\n\nN.")
    text = re.sub(r'\n\n---+\s*\n[\s\S]{0,40}$', '', text)
    text = re.sub(r'\n\n---+\s*$', '', text)
    # Strip standalone short reference stubs at end (e.g. "\n\nN." or "\n\n1.")
    text = re.sub(r'\n\n[A-Z0-9]\.\s*$', '', text)
    return text
