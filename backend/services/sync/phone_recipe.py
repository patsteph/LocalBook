"""The Mac's answer recipe, handed to a paired phone so both answer the same way.

A phone answering offline has to behave like the Mac, and "keeping two copies in step" is how they
drift. So the Mac publishes the recipe as DATA, built by calling its own functions:

  * the three system prompts, straight from `rag_generation.get_prompt_for_query_type`
  * the question classifier's pattern lists (`rag_query_analyzer`) — the phone runs the same rules
  * the user-prompt template, generation settings and retrieval numbers
  * the output-cleaning steps (regexes) the Mac applies to a model's answer
  * conformance vectors: sample questions with the type the Mac's classifier gives them, and raw
    answers with the cleaned text the Mac's cleaner gives them. The phone re-runs its own code on
    them after every sync and reports any mismatch, so a drift is caught instead of trusted.

`recipe_version` changes whenever any of it changes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

PROTO = 1

# Mirrors rag_generation.clean_llm_output, step for step (pattern, replacement, flags).
# tests/test_phone_recipe.py applies these with Python's re and asserts the result equals the Mac's own
# function on a set of samples, so this copy cannot silently drift from it.
CLEAN_STEPS: List[Dict[str, Any]] = [
    {"pattern": r"\\boxed\{([^}]*)\}", "replace": r"\1", "flags": []},
    {"pattern": r"\\text\{([^}]*)\}", "replace": r"\1", "flags": []},
    {"pattern": r"\\textbf\{([^}]*)\}", "replace": r"\1", "flags": []},
    {"pattern": r"\\textit\{([^}]*)\}", "replace": r"\1", "flags": []},
    {"pattern": r"\\(boxed|text|textbf|textit)\b", "replace": "", "flags": []},
    {"pattern": (r"\n\s*(?:\*\*)?(?:supporting\s+)?(?:references|sources?|citations?|bibliography|cited\s+sources|"
                 r"key\s+references|footnotes)(?:\*\*)?[:\s]*(?:\n|$).*"), "replace": "",
     "flags": ["dotall", "ignorecase"], "keep_original_if_empty": True},
    {"pattern": r"\n*User context:.*$", "replace": "", "flags": ["dotall", "ignorecase"]},
    {"pattern": r"\n*Citation.*should not be included.*$", "replace": "", "flags": ["dotall", "ignorecase"]},
    {"pattern": r"\n\s*\*Note[:\s].*$", "replace": "", "flags": ["dotall", "ignorecase"]},
    {"pattern": r"\s*\[\d+\]\s*\[\d+\.?\s*$", "replace": "", "flags": []},
    {"pattern": r"\s*\[\d[\d,\s]*\.?\s*$", "replace": "", "flags": []},
    {"pattern": r"\n{3,}", "replace": "\n\n", "flags": []},
]

SAMPLE_QUESTIONS = [
    "How many demos did Chris do in Q1?", "What is the onboarding length at Netflix?", "Who is the author of this report?",
    "When was the pilot launched?", "List the top reasons customers churn", "Compare RAG and agentic AI",
    "Explain how hybrid search works", "What are the differences between local and hosted models?",
    "What are the pros and cons of running models on a phone?", "Walk me through the sync design",
    "Tell me about onboarding", "How does Agentic AI differ from RAG?", "What is needed to set up RAG on a laptop?",
    "What is Agentic AI and how does it help teams?", "Why do chunks need a token limit? And what happens when they exceed it?",
    "Summarize the main themes in these notes", "What did we decide about the Q3 roadmap and who owns each item and why?",
    "name the vendors mentioned", "How much did the pilot cost?", "Critique the proposed architecture",
    "hello", "What are the top 5 risks?", "Break down the budget by quarter",
    "Describe the relationship between memory and the knowledge graph in the application today please",
]

SAMPLE_ANSWERS = [
    "Agentic RAG differs [1].\n\nReferences:\n[1] doc.pdf",
    "Sources:\n- a\n- b\nThen some actual answer that should survive?",
    "The result is \\boxed{42} and \\textbf{bold} text [1].",
    "A fine answer.\n\n\n\nWith blank lines [2]. [3",
    "Answer with a note [1].\n*Note: this is generated",
    "Plain answer with nothing to clean.",
    "Result [1][2.",
]


def _classification_vectors() -> List[Dict[str, str]]:
    from services import rag_query_analyzer
    return [{"question": q, "type": rag_query_analyzer.classify_query(q)} for q in SAMPLE_QUESTIONS]


def _expansion_vectors() -> List[Dict[str, str]]:
    from services import rag_query_analyzer
    qs = ["How many demos did Chris do in Q1?", "What did Mike say about revenue and customers in FY2026?",
          "How does Agentic AI differ from RAG?", "Tom and Pat met about the trial", "no expansions here"]
    return [{"question": q, "expanded": rag_query_analyzer.expand_query(q)} for q in qs]


CITATION_FIX_SAMPLES = [
    ("Fine [1] and [2, 3].", 3),
    ("Wrong section [16.7.3] and [562].", 5),
    ("Mixed [1, 562] and [2], [16.2].", 4),
    ("Beyond count [6].\n[7] starts a line.", 5),
    ("Ranges [1-3] and words [see 2] stay.", 5),
]


QUALITY_SAMPLES = [
    ("How long is onboarding?", "Onboarding is not specified in the provided sources.", "factual"),
    ("How many demos?", "Chris did 12 demos in Q1 [1].", "factual"),
    ("What is RAG?", "I cannot find this in your notes at all.", "synthesis"),
    ("What is RAG?", "Short.", "synthesis"),
    ("What is RAG?", "RAG retrieves passages before answering [N].", "synthesis"),
    ("Total?", "The total is X demos for the quarter, replace 'X' later.", "factual"),
    ("Compare", "A fine comparison of the two systems with no numbers at all.", "complex"),
    ("Who?", "NOTE TO USER: fill in the name here please.", "synthesis"),
]
VARIANT_SAMPLES = ["How many demos did Pat Stephens do in Q1?", "what is fy planning", "Tell me about Netflix onboarding",
                   "total revenue q3 and q4", "how long is onbaording at Netflix", "plain lower case question"]


CONFIDENCE_SAMPLES = [(0.0266, 0.5), (0.00007, 0.9), (0.5, 0.0), (None, 0.8), (0.0084, 0.0 + 0.3), (0.3, 0.2), (0.9, 1.0), (0.02, 0.0)]


def _confidence_vectors() -> List[Dict[str, Any]]:
    from services import rag_context
    return [{"rerank": r, "rrf": f, "confidence": rag_context.citation_confidence(r, f)}
            for r, f in CONFIDENCE_SAMPLES]


def _quality_vectors() -> List[Dict[str, Any]]:
    from services import rag_query_analyzer
    return [{"question": q, "answer": a, "type": t,
             "ok": bool(rag_query_analyzer.check_answer_quality(q, a, t)[0])} for q, a, t in QUALITY_SAMPLES]


def _variant_vectors() -> List[Dict[str, Any]]:
    from services import rag_query_analyzer
    return [{"question": q, "variants": rag_query_analyzer.generate_query_variants(q)} for q in VARIANT_SAMPLES]


def _format_vectors() -> List[Dict[str, str]]:
    from services import rag_query_analyzer
    qs = SAMPLE_QUESTIONS + ["Write me the code for a parser", "Give me a step by step guide", "What are the key risks?",
                             "table of costs for the pilot"]
    return [{"question": q, "hint": rag_query_analyzer.detect_response_format(q)} for q in qs]


def _citation_fix_vectors() -> List[Dict[str, Any]]:
    from services import rag_generation
    return [{"raw": a, "count": n, "fixed": rag_generation.fix_citations(a, n)} for a, n in CITATION_FIX_SAMPLES]


def _clean_vectors() -> List[Dict[str, str]]:
    from services import rag_generation
    return [{"raw": a, "cleaned": rag_generation.clean_llm_output(a)} for a in SAMPLE_ANSWERS]


def recipe() -> Dict[str, Any]:
    from config import settings
    from services import mlx_engine, rag_generation, rag_query_analyzer
    from services.sync import phone_personal

    body: Dict[str, Any] = {
        "proto": PROTO,
        "system_prompts": {t: rag_generation.get_prompt_for_query_type(t, 5, 0.5)
                           for t in ("factual", "synthesis", "complex")},
        "classify": {
            "complex_patterns": list(rag_query_analyzer.COMPLEX_PATTERNS),
            "factual_patterns": list(rag_query_analyzer.FACTUAL_PATTERNS),
            "long_question_chars": 100,
            "multi_part_phrases": [" and what ", " and how ", " and who ", " and why ", " and which ", " and where "],
            "multi_part_regex": r"\band\b.{5,}\?",
        },
        # How the Mac frames the question and sources for its model (rag_generation.generate_answer).
        "user_prompt": "Sources:\n{context}\n\nQ: {question}\n\nAnswer the question, citing sources inline as [N]. "
                       "Do not list references at the end.",
        "context_item": "[{n}] {text}",
        "context_separator": "\n\n",
        # The Mac's chat-answer decoding (llm_service.generate_text → mlx_engine._decode_kwargs): 500 tokens,
        # temperature 0.3, top-p and repetition penalty from the engine's own constants.
        "generation": {"temperature": 0.3, "max_tokens": 500, "top_p": float(mlx_engine._MLX_TOP_P),
                       "repetition_penalty": float(mlx_engine._MLX_REP_PENALTY),
                       "repetition_context_size": int(mlx_engine._MLX_REP_CONTEXT),
                       # The phone's model is wordier than the Mac's and would be cut off at max_tokens mid-sentence;
                       # this line goes on the end of its system prompt (the Mac does not need it).
                       "length_hint": "Keep the whole answer under 250 words and finish every sentence."},
        # rag_search.adaptive_search → hybrid_search(k = overcollect * 2): each ranker contributes k * 2
        # candidates, they are fused with Reciprocal Rank Fusion (0-based ranks, 1000 when absent) to k, the
        # reranker keeps top_k + 1. BM25 is rank_bm25's BM25Okapi over lower-cased whitespace tokens.
        "retrieval": {"top_k": 4, "overcollect": int(settings.retrieval_overcollect), "rerank_keep": 5,
                      "hybrid_k": int(settings.retrieval_overcollect) * 2,
                      "vector_candidates": int(settings.retrieval_overcollect) * 4,
                      "bm25_candidates": int(settings.retrieval_overcollect) * 4,
                      "rrf_k": 60, "rrf_missing_rank": 1000,
                      "bm25": {"k1": 1.5, "b": 0.75, "epsilon": 0.25},
                      "reranker": {"enabled": bool(settings.use_reranker), "model": settings.reranker_model,
                                   "precision": getattr(settings, "reranker_precision", "int8")}},
        # Ordered [term, expansion] pairs: the order the expansions are appended in changes the expanded text.
        "query_expansion": {"expansions": [[k, v] for k, v in rag_query_analyzer.EXPANSIONS.items()],
                            "name_expansions": [[k, v] for k, v in rag_query_analyzer.NAME_EXPANSIONS.items()]},
        # rag_context.build_citations_and_context: how a reranked passage becomes a citation, which are kept.
        "citations": {"rerank_floor": 0.01, "rerank_scale": 1.4, "rerank_offset": 0.1,
                      "rrf_base": 0.5, "rrf_scale": 0.4, "min_confidence": 0.20, "very_low_confidence": 0.10,
                      "fallback_keep": 3,
                      # rag_context.citation_confidence: the higher of the rerank-based and RRF-based values wins
                      "combine": "max",
                      "no_answer": ("I don't have enough relevant information in your documents to answer this question "
                                    "accurately. Try uploading more documents related to this topic, or rephrase your question.")},
        # rag_query_analyzer.detect_response_format: the first matching rule's hint ends a factual question's prompt.
        "format_rules": [[pat, hint] for pat, hint in rag_query_analyzer.FORMAT_RULES],
        # source_router, and the entity/graph numbers: how the Mac re-orders reranked passages before citing them.
        "routing": phone_personal.routing(),
        "personalization": phone_personal.RULES,
        # rag_query_analyzer.check_answer_quality / generate_query_variants and rag_search.corrective_retrieval:
        # when an answer looks weak the Mac retries retrieval with query variants and answers again.
        "quality": {"min_chars": rag_query_analyzer.QUALITY_MIN_CHARS,
                    "failure_phrases": rag_query_analyzer.QUALITY_FAILURE_PHRASES,
                    "placeholder_regex": rag_query_analyzer.QUALITY_PLACEHOLDER_REGEX,
                    "placeholder_tokens": rag_query_analyzer.QUALITY_PLACEHOLDER_TOKENS,
                    "meta_phrases": rag_query_analyzer.QUALITY_META_PHRASES},
        "variants": {"expansions": [[k, v] for k, v in rag_query_analyzer.VARIANT_EXPANSIONS.items()],
                     "count_words": rag_query_analyzer.VARIANT_COUNT_WORDS,
                     "count_suffix": rag_query_analyzer.VARIANT_COUNT_SUFFIX,
                     "entity_regex": rag_query_analyzer.VARIANT_ENTITY_REGEX, "max": rag_query_analyzer.VARIANT_MAX},
        "corrective": {"hybrid_k": int(settings.retrieval_overcollect),
                       "vector_candidates": int(settings.retrieval_overcollect) * 2,
                       "bm25_candidates": int(settings.retrieval_overcollect) * 2,
                       "result_cap": int(settings.retrieval_overcollect) * 2, "rerank_keep": 5},
        "clean_steps": CLEAN_STEPS,
        "conformance": {"classification": _classification_vectors(), "cleaning": _clean_vectors(),
                        "expansion": _expansion_vectors(),
                        "citation_fix": _citation_fix_vectors(),
                        "format": _format_vectors(),
                        "quality": _quality_vectors(),
                        "confidence": _confidence_vectors(),
                        "variants": _variant_vectors(),
                        "routing": phone_personal.routing_vectors(SAMPLE_QUESTIONS),
                        "personal": phone_personal.personal_vectors()},
    }
    version = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    return {"recipe_version": version, **body}
