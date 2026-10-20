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


def _clean_vectors() -> List[Dict[str, str]]:
    from services import rag_generation
    return [{"raw": a, "cleaned": rag_generation.clean_llm_output(a)} for a in SAMPLE_ANSWERS]


def recipe() -> Dict[str, Any]:
    from config import settings
    from services import mlx_engine, rag_generation, rag_query_analyzer

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
                       "repetition_context_size": int(mlx_engine._MLX_REP_CONTEXT)},
        "retrieval": {"top_k": 4, "overcollect": int(settings.retrieval_overcollect), "rerank_keep": 5,
                      "rrf_k": 60, "candidates_per_ranker": 50,
                      "reranker": {"enabled": bool(settings.use_reranker), "model": settings.reranker_model,
                                   "precision": getattr(settings, "reranker_precision", "int8")}},
        "clean_steps": CLEAN_STEPS,
        "conformance": {"classification": _classification_vectors(), "cleaning": _clean_vectors()},
    }
    version = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    return {"recipe_version": version, **body}
