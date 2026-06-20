"""Scoring engine — deterministic checks + LLM-as-judge for quality assessment.

Provides both objective scoring (must_contain, schema validation, format checks)
and subjective scoring (LLM evaluates answer quality using a different model).
"""

import re
import json
import time
from typing import Optional
from evaluator.models import EvalResult, _score_to_grade
import logging
logger = logging.getLogger(__name__)


# ─── Deterministic Scoring ───────────────────────────────────────────────────

def score_must_contain(output: str, expected_facts: list[str], case_insensitive: bool = True) -> int:
    """Score based on presence of expected facts in output. Returns 0-100."""
    if not expected_facts:
        return 100
    if not output:
        return 0

    text = output.lower() if case_insensitive else output
    found = sum(1 for fact in expected_facts if fact.lower() in text)
    return int((found / len(expected_facts)) * 100)


def score_json_validity(output: str) -> tuple[int, str]:
    """Validate JSON output. Returns (score 0-100, error_reason)."""
    if not output:
        return 0, "Empty output"

    # Try to extract JSON from output (may have surrounding text)
    json_str = output.strip()

    # Try direct parse
    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, (dict, list)):
            return 100, ""
        return 80, "Parsed but unexpected type"
    except json.JSONDecodeError as _e:
        logger.debug(f"[scoring] {type(_e).__name__}: {_e}")

    # Try extracting JSON from markdown code blocks
    code_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', json_str)
    if code_block:
        try:
            json.loads(code_block.group(1).strip())
            return 90, ""  # Slight penalty for needing extraction
        except json.JSONDecodeError:
            return 20, "Found code block but invalid JSON inside"

    # Try finding { } or [ ] boundaries
    start = json_str.find('{')
    end = json_str.rfind('}')
    if start >= 0 and end > start:
        try:
            json.loads(json_str[start:end + 1])
            return 80, ""  # Penalty for extra text around JSON
        except json.JSONDecodeError as _e:
            logger.debug(f"[scoring] {type(_e).__name__}: {_e}")

    return 0, "No valid JSON found"


def score_format_compliance(output: str, expected_format: dict) -> int:
    """Score format compliance (numbered items, paragraph count, forbidden elements).
    
    expected_format keys:
        - numbered_items: int — expected count of numbered items
        - paragraph_count: int — expected number of paragraphs
        - max_sentences_per_item: int — max sentences per numbered item
        - forbidden_elements: list[str] — strings that must NOT appear
    """
    if not output:
        return 0

    score = 100
    penalties = []

    # Check numbered items
    if "numbered_items" in expected_format:
        expected = expected_format["numbered_items"]
        # Count numbered items (1., 2., etc. or 1) 2) etc.)
        items = re.findall(r'^\s*\d+[\.\)]\s', output, re.MULTILINE)
        actual = len(items)
        if actual != expected:
            diff = abs(actual - expected)
            penalty = min(50, diff * 15)
            score -= penalty
            penalties.append(f"Expected {expected} items, found {actual}")

    # Check paragraph count
    if "paragraph_count" in expected_format:
        expected = expected_format["paragraph_count"]
        # Split by double newline, filter empty
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', output) if p.strip()]
        actual = len(paragraphs)
        if actual != expected:
            diff = abs(actual - expected)
            penalty = min(50, diff * 15)
            score -= penalty
            penalties.append(f"Expected {expected} paragraphs, found {actual}")

    # Check forbidden elements
    if "forbidden_elements" in expected_format:
        for elem in expected_format["forbidden_elements"]:
            if elem in output:
                score -= 15
                penalties.append(f"Contains forbidden element: '{elem}'")

    # ── IFEval-style constraints ──────────────────────────────────────────
    # Word count constraints (max and/or min)
    if "max_word_count" in expected_format or "min_word_count" in expected_format:
        word_count = len(output.split())
        max_words = expected_format.get("max_word_count")
        min_words = expected_format.get("min_word_count")
        if max_words is not None and word_count > max_words:
            overage = word_count - max_words
            penalty = min(40, int(overage * 100 / max_words))
            score -= penalty
            penalties.append(f"Exceeded word limit: {word_count} > {max_words}")
        if min_words is not None and word_count < min_words:
            shortfall = min_words - word_count
            penalty = min(40, int(shortfall * 100 / min_words))
            score -= penalty
            penalties.append(f"Below word minimum: {word_count} < {min_words}")

    # Required keywords (must include all, case-insensitive substring match)
    if "required_keywords" in expected_format:
        output_lower = output.lower()
        missing = [kw for kw in expected_format["required_keywords"]
                   if kw.lower() not in output_lower]
        if missing:
            # Each missing keyword is a major violation
            penalty = min(50, len(missing) * 25)
            score -= penalty
            penalties.append(f"Missing required keywords: {missing}")

    # Banned keywords (must NOT include any, whole-word match for accuracy)
    # Whole-word matching prevents "vectorize" from matching "vector"
    if "banned_keywords" in expected_format:
        violations = []
        for kw in expected_format["banned_keywords"]:
            # Word-boundary regex, case-insensitive
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, output, re.IGNORECASE):
                violations.append(kw)
        if violations:
            # Banned keyword violations are severe — IFEval treats this as binary
            penalty = min(70, len(violations) * 35)
            score -= penalty
            penalties.append(f"Used banned keywords: {violations}")

    return max(0, score)


def score_output_length(output: str, min_words: int = 0, max_words: int = 0) -> int:
    """Score based on word count being within expected range. Returns 0-100."""
    if not output:
        return 0

    word_count = len(output.split())

    if min_words > 0 and word_count < min_words:
        ratio = word_count / min_words
        return max(0, int(ratio * 100))

    if max_words > 0 and word_count > max_words:
        overage = (word_count - max_words) / max_words
        return max(0, int(100 - overage * 50))

    return 100


def score_has_citations(output: str, min_citations: int = 1) -> int:
    """Score based on presence of citation markers [1], [2], etc."""
    if not output:
        return 0
    citations = re.findall(r'\[(\d+)\]', output)
    unique_citations = len(set(citations))
    if unique_citations >= min_citations:
        return 100
    elif unique_citations > 0:
        return int((unique_citations / min_citations) * 100)
    return 0


def score_has_headings(output: str, min_headings: int = 1) -> int:
    """Score based on presence of markdown headings."""
    if not output:
        return 0
    headings = re.findall(r'^#{1,4}\s+\S', output, re.MULTILINE)
    if len(headings) >= min_headings:
        return 100
    elif headings:
        return int((len(headings) / min_headings) * 100)
    return 0


# ─── Semantic Scoring (RAGAS-style, industry standard) ─────────────────────

async def score_semantic_similarity(answer: str, reference_answer: str) -> int:
    """Semantic similarity between generated answer and reference answer.
    
    Uses the local embedding model to compute cosine similarity. This is the
    industry-standard "Answer Correctness" metric used by RAGAS, TruLens, etc.
    Replaces brittle keyword matching.
    
    Returns 0-100 (cosine sim mapped: 0.0→0, 0.5→50, 0.85+→100).
    """
    if not answer or not reference_answer:
        return 0
    try:
        from services.rag_embeddings import encode_async
        import numpy as np
        embeddings = await encode_async([answer, reference_answer])
        a, b = embeddings[0], embeddings[1]
        # Cosine similarity
        cos_sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        # Map: 0.0→0, 0.5→50, 0.85→100 (typical good RAG answers cluster 0.7-0.9)
        # Linear stretch from [0.3, 0.85] → [0, 100]
        normalized = max(0.0, min(1.0, (cos_sim - 0.3) / 0.55))
        return int(normalized * 100)
    except Exception as e:
        logger.warning(f"[scoring] semantic similarity failed: {e}")
        return 50  # Neutral fallback


def score_context_recall(citations: list[dict], gold_chunk_marker) -> int:
    """Context Recall: Did retrieval find a chunk containing the answer?
    
    Industry-standard "Context Recall" metric. Accepts a single marker string
    OR a list of acceptable markers (any-of matching). When multiple markers
    are provided, finding ANY ONE of them counts as full recall — this handles
    cases where the same fact appears in multiple chunks of the source.
    
    Also checks parent_text and snippet for the marker, since the citation
    .text field may be the chunked sub-section while parent_text holds the
    enclosing paragraph.
    
    100 = at least one gold-chunk marker was retrieved
    0 = retrieval missed every relevant chunk
    """
    if not citations or not gold_chunk_marker:
        return 0
    
    # Normalize markers to a list for any-of matching
    markers = gold_chunk_marker if isinstance(gold_chunk_marker, list) else [gold_chunk_marker]
    markers_lower = [m.lower() for m in markers if m]
    if not markers_lower:
        return 0
    
    for c in citations:
        # Check text, parent_text, AND snippet — chunkers may split the marker
        # phrase across boundaries, so checking the broader context catches
        # legitimate retrievals that hit the right paragraph.
        haystack = " ".join([
            (c.get("text", "") or ""),
            (c.get("parent_text", "") or ""),
            (c.get("snippet", "") or ""),
        ]).lower()
        for marker in markers_lower:
            if marker in haystack:
                return 100
    return 0


async def score_faithfulness(answer: str, citations: list[dict], judge_model: str) -> int:
    """Faithfulness: Does the answer ONLY use information from retrieved context?
    
    LLM judge evaluates whether claims in the answer are supported by citations.
    This catches hallucination — the most critical RAG failure mode.
    """
    if not answer or not citations:
        return 50
    
    from services.ollama_service import ollama_service
    context = "\n---\n".join(c.get("text", "")[:500] for c in citations[:4])
    
    prompt = f"""Evaluate if this answer is FAITHFUL to the provided context.
Faithful = every claim is directly supported by the context.
Unfaithful = answer contains information NOT in the context (hallucination).

Context:
{context[:2000]}

Answer: {answer[:1500]}

Respond ONLY with JSON: {{"faithful": <0-100>, "reason": "<brief>"}}.
100 = fully faithful, 50 = mixed, 0 = mostly hallucinated."""
    
    try:
        result = await ollama_service.generate(
            prompt=prompt,
            model=judge_model,
            system="You are a strict faithfulness evaluator. Return only valid JSON.",
            temperature=0.1,
            num_predict=80,
            timeout=30.0,
        )
        text = (result or {}).get("response", "")
        try:
            parsed = json.loads(text.strip())
            return max(0, min(100, int(parsed.get("faithful", 50))))
        except (json.JSONDecodeError, ValueError):
            match = re.search(r'"faithful"\s*:\s*(\d+)', text)
            if match:
                return max(0, min(100, int(match.group(1))))
            return 50
    except Exception as e:
        logger.warning(f"[scoring] faithfulness check failed: {e}")
        return 50


# ─── LLM-as-Judge Scoring ───────────────────────────────────────────────────

async def llm_judge_score(
    question: str,
    answer: str,
    judge_model: str,
    criteria: str = "accuracy, completeness, and coherence",
) -> int:
    """Use a secondary LLM to evaluate answer quality. Returns 0-100.
    
    Uses the Ollama API directly — the judge model should be different from
    the model that generated the answer to avoid self-evaluation bias.
    """
    # v1.8.0: route via ollama_client (provider-aware). Works for both Ollama
    # and llama-server sidecar judge models, and respects ollama_base_url.
    from services.ollama_service import ollama_service

    prompt = f"""You are an expert evaluator. Score the following AI answer on a scale of 0-100.

Evaluate based on: {criteria}

Question: {question}

Answer: {answer[:3000]}

Respond with ONLY a JSON object: {{"score": <0-100>, "reason": "<one sentence>"}}"""

    try:
        result = await ollama_service.generate(
            prompt=prompt,
            model=judge_model,
            system="You are a strict but fair answer quality evaluator. Always respond with valid JSON only.",
            temperature=0.1,
            num_predict=100,
            timeout=30.0,
        )

        # ollama_client.generate always returns a dict with "response" (possibly
        # containing "Error: ..." on transport failure). Treat success as non-empty
        # non-error text.
        if result and isinstance(result, dict):
            text = result.get("response", "") or ""
            # Extract score from JSON
            try:
                parsed = json.loads(text.strip())
                return max(0, min(100, int(parsed.get("score", 50))))
            except (json.JSONDecodeError, ValueError):
                # Try regex extraction
                match = re.search(r'"score"\s*:\s*(\d+)', text)
                if match:
                    return max(0, min(100, int(match.group(1))))
                return 50  # Default to middle if can't parse
        return 50  # result was not a usable dict
    except Exception as e:
        print(f"[SCORING] LLM judge failed: {e}")
        return 50  # Default score on failure


# ─── Composite Scoring ──────────────────────────────────────────────────────

def compute_weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Compute weighted average from named scores and weights.
    
    scores: {"accuracy": 85, "format": 90, "speed": 70}
    weights: {"accuracy": 0.4, "format": 0.3, "speed": 0.3}
    """
    total_weight = sum(weights.values())
    if total_weight == 0:
        return 0.0

    weighted_sum = sum(
        scores.get(k, 0) * w for k, w in weights.items()
    )
    return weighted_sum / total_weight


def compute_category_score(results: list[EvalResult]) -> tuple[float, str]:
    """Compute category score from individual test results.
    Returns (score, grade)."""
    if not results:
        return 0.0, "F"

    # Filter out skipped tests
    active = [r for r in results if not r.skipped]
    if not active:
        return 0.0, "F"

    avg = sum(r.overall_score for r in active) / len(active)
    return round(avg, 1), _score_to_grade(avg)


def compute_overall_score(
    category_scores: dict[str, float],
    weights: dict[str, int],
) -> tuple[float, str]:
    """Compute overall score from category scores with weights.
    Returns (score, grade)."""
    total_weight = 0
    weighted_sum = 0.0

    for category, weight in weights.items():
        if category in category_scores:
            weighted_sum += category_scores[category] * weight
            total_weight += weight

    if total_weight == 0:
        return 0.0, "F"

    score = weighted_sum / total_weight
    return round(score, 1), _score_to_grade(score)
