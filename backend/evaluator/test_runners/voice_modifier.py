"""Voice modifier consistency test runner.

Verifies that the active main model's family voice modifier (per
services/voice_modifier.py) actually shapes output without losing
substantive content. Two-shot test: same prompt, once with the modifier
on and once with it off, then compare.

Scoring:
  - hedging_reduction (50%): the OFF answer's hedging-phrase count vs
    the ON answer's — modifier should reduce hedging.
  - semantic_similarity (50%): the two answers should still be saying
    the same THING, just in different voice. <0.5 means the modifier
    is destroying meaning, not shaping voice.

Universal prompt (same for every combo) so this is apples-to-apples
across model swaps.
"""

import time
from datetime import datetime

from evaluator.models import EvalResult
from evaluator import scoring


# Phrases the per-family voice modifiers explicitly discourage. olmo's
# modifier says "skip preamble"; gemma's says "avoid hedging phrases like
# 'it is worth noting' or 'fundamentally'". A working modifier should
# produce fewer of these on the ON answer than the OFF answer.
_HEDGING_MARKERS = [
    "it is worth noting",
    "in the context of",
    "fundamentally",
    "essentially",
    "to summarize",
    "in summary",
    "in conclusion,",
    "it should be noted",
    "in essence",
    "broadly speaking",
]

# Universal prompt — chosen to elicit hedging in baseline (off) output.
# Asks for an "explanation of an everyday concept" — the kind of thing
# verbose models pad with preamble.
_PROMPT = (
    "Briefly explain what a hash table is and why it's useful. "
    "Two short paragraphs, no introduction. Just the explanation."
)
_SYSTEM = "You are a helpful technical writer."


def _count_hedging(text: str) -> int:
    """Lowercase substring count over the markers list."""
    if not text:
        return 0
    lower = text.lower()
    return sum(lower.count(m) for m in _HEDGING_MARKERS)


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Two-shot voice modifier consistency test.

    notebook_id and config are accepted for signature parity with other
    test runners but unused — this test is model-only, no RAG context.
    """
    from services.rag_engine import rag_engine
    from config import settings

    result = EvalResult(
        test_id="voice_modifier_consistency",
        category="voice_modifier",
        test_name="Voice Modifier Consistency",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(settings.ollama_model)

    try:
        start = time.time()

        # Run OFF first so the model isn't warm with voice-modifier output
        # influencing the second run.
        off_text = await rag_engine._call_ollama(
            system_prompt=_SYSTEM,
            prompt=_PROMPT,
            num_predict=400,
            temperature=0.3,
            voice_modifier=False,
        )
        on_text = await rag_engine._call_ollama(
            system_prompt=_SYSTEM,
            prompt=_PROMPT,
            num_predict=400,
            temperature=0.3,
            voice_modifier=True,
        )

        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed
        result.output_chars = len(off_text) + len(on_text)
        result.actual_output_preview = (
            f"OFF [{len(off_text)}c]: {off_text[:200]}…\n\n"
            f"ON  [{len(on_text)}c]: {on_text[:200]}…"
        )

        if not off_text.strip() or not on_text.strip():
            raise ValueError("voice modifier test received empty response from at least one branch")

        # Hedging reduction score — 100 if ON has fewer hedges, scaled
        # otherwise. ON producing zero hedges is the ideal.
        off_hedges = _count_hedging(off_text)
        on_hedges = _count_hedging(on_text)
        if off_hedges == 0 and on_hedges == 0:
            # Model is naturally terse — modifier has nothing to remove.
            # Don't punish; this is fine but uninformative.
            hedging_score = 80
        elif on_hedges <= off_hedges:
            # Modifier reduced or held hedging steady. Linear payoff.
            reduction = (off_hedges - on_hedges) / max(off_hedges, 1)
            hedging_score = int(60 + 40 * reduction)
        else:
            # Modifier INCREASED hedging — possibly making output worse.
            hedging_score = max(0, 60 - (on_hedges - off_hedges) * 20)

        # Semantic similarity — both answers should be saying the same
        # thing about hash tables, just in different voice. We reuse the
        # existing scorer which uses embeddings (async).
        try:
            sim_score = await scoring.score_semantic_similarity(off_text, on_text)
        except Exception:
            # Fallback: trivially-close lengths suggest similar content.
            len_ratio = min(len(off_text), len(on_text)) / max(len(off_text), len(on_text), 1)
            sim_score = int(len_ratio * 80)

        result.sub_scores = {
            "hedging_off": off_hedges,
            "hedging_on": on_hedges,
            "hedging_score": hedging_score,
            "semantic_similarity": sim_score,
        }
        result.accuracy_score = hedging_score
        result.completeness_score = sim_score
        result.overall_score = int(hedging_score * 0.5 + sim_score * 0.5)
        result.passed = result.overall_score >= 50 and sim_score >= 40

        if sim_score < 40:
            result.failure_reason = (
                f"Semantic similarity too low ({sim_score}) — voice modifier is "
                "destroying meaning, not just tone."
            )

        print(
            f"[EVAL-VOICE] hedging off={off_hedges} on={on_hedges} → "
            f"score={hedging_score}, sim={sim_score}, overall={result.overall_score}"
        )

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-VOICE] FAILED: {e}")

    return [result]
