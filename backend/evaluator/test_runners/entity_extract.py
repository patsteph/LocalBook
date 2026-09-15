"""Entity extraction test runner — precision and recall against labelled text.

Entities feed the knowledge graph, the Constellation view, cross-notebook connections AND
retrieval, so a model that extracts badly degrades several features at once — quietly, because
nothing downstream announces "the entities were wrong". This had no coverage.

JUDGE-FREE by construction: the passage is fixed, the entities in it are enumerated by hand, and
scoring is set arithmetic. No LLM decides whether the answer was good, so the number means the
same thing for every candidate model — which is what makes it usable for a model swap.

Calls `_extract_with_llm` rather than `extract_from_text`, deliberately. The public method takes
`notebook_id` / `source_id` and PERSISTS what it finds; an evaluation must not write entities
into the user's graph as a side effect of measuring.

Scoring is recall-weighted (70/30). A missed entity is a hole in the graph; a spurious one is
noise that later filtering can still catch, and the denylist already trims common junk.
"""
import time
from datetime import datetime

from evaluator.models import EvalResult

# One passage, entities enumerated by hand. Deliberately mixes the easy (capitalised proper
# nouns) with the ones models usually drop: a versioned product name, an acronym expanded in
# place, and an organisation that appears only in possessive form.
_PASSAGE = (
    "In March 2024, Anthropic released Claude 3, competing directly with OpenAI's GPT-4. "
    "The model was trained on hardware from NVIDIA, using clusters coordinated by Amazon Web "
    "Services (AWS). Dario Amodei, Anthropic's CEO, described the release as a step toward "
    "Constitutional AI at scale. Google DeepMind responded with Gemini, while Meta continued "
    "to release open weights under the Llama name."
)

# Lowercased; any-of aliases per entity, because "AWS" and "Amazon Web Services" are one thing.
_EXPECTED = [
    ["anthropic"],
    ["claude 3", "claude"],
    ["openai"],
    ["gpt-4", "gpt4"],
    ["nvidia"],
    ["amazon web services", "aws"],
    ["dario amodei"],
    ["google deepmind", "deepmind"],
    ["gemini"],
    ["meta"],
    ["llama"],
]


def _matched(found_names: list, aliases: list) -> bool:
    """An expected entity counts as found if any alias appears in any extracted name.

    Substring rather than equality: models legitimately return "Anthropic PBC" or
    "Claude 3 Opus", and calling those misses would measure formatting, not extraction.
    """
    return any(alias in name for alias in aliases for name in found_names)


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list:
    from config import settings

    result = EvalResult(
        test_id="entity_extract_precision_recall",
        category="entity_extract",
        test_name="Entity Extraction: precision / recall",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(getattr(settings, "fast_model", "") or settings.main_model)
    result.input_chars = len(_PASSAGE)

    try:
        from services.entity_extractor import entity_extractor
    except Exception as e:
        result.mark_skipped(f"entity extractor unavailable ({type(e).__name__})")
        return [result]

    try:
        start = time.time()
        entities = await entity_extractor._extract_with_llm(_PASSAGE)
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        names = []
        for e in entities or []:
            n = getattr(e, "name", None) or (e.get("name") if isinstance(e, dict) else None)
            if n:
                names.append(str(n).lower())

        found = [grp for grp in _EXPECTED if _matched(names, grp)]
        recall = len(found) / len(_EXPECTED)
        # Precision approximated: how many extracted names correspond to something expected.
        # Approximate because the passage does contain other legitimate entities (a date, a
        # concept) — so this is a noise indicator, not a strict precision figure. It is
        # weighted lightly for exactly that reason.
        useful = sum(1 for n in names if any(a in n for grp in _EXPECTED for a in grp))
        precision = (useful / len(names)) if names else 0.0

        result.overall_score = int(100 * (0.7 * recall + 0.3 * precision))
        result.accuracy_score = int(100 * recall)
        result.passed = recall >= 0.6
        result.output_chars = len(names)
        missed = [grp[0] for grp in _EXPECTED if grp not in found]
        result.sub_scores = {
            "recall": round(recall, 3),
            "precision_approx": round(precision, 3),
            "expected": len(_EXPECTED),
            "found": len(found),
            "extracted_total": len(names),
            "missed": missed,
        }
        result.actual_output_preview = f"found {len(found)}/{len(_EXPECTED)}: {', '.join(names[:12])}"
        if not result.passed:
            result.failure_reason = (
                f"recall {recall:.0%} — missed {', '.join(missed[:6])}"
            )
        print(f"[EVAL-ENTITY] recall={recall:.0%} precision~{precision:.0%} "
              f"score={result.overall_score} ({elapsed:.0f}ms) missed={missed[:4]}")

    except Exception as e:
        result.passed = False
        result.overall_score = 0
        result.failure_reason = str(e)[:200]
        print(f"[EVAL-ENTITY] FAILED: {e}")

    return [result]
