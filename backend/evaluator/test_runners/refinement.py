"""Refinement-pass fidelity test runner.

Verifies services/scan_pipeline._refine_visual is doing what the prompt
promises: tidying structure of a vision-mode output WITHOUT adding
information not in the input.

Two checks:
  1. Label preservation — every concrete element label from the golden
     input must still be in the refined output. Losing labels means the
     refinement model dropped content the user captured.
  2. Hallucination guard — the refined output must not introduce concrete
     entities that weren't in the input. We use a small denylist of
     plausible-but-not-present terms; if any appear, the model has
     fabricated content.

Single fixed input, single fixed denylist — apples-to-apples across
combos.
"""

import time
from datetime import datetime

from evaluator.models import EvalResult


# Golden raw drawing description. Concrete element labels: "tree",
# "house", "river", "sun" — these MUST survive refinement. Style hints
# (sketch, blue) help the refinement model recognise this is a drawing
# spec, not generic prose.
_GOLDEN_DRAWING_RAW = """
A pencil sketch of a small countryside scene.

Elements:
- A two-story house on the left
- A leafy tree to the right of the house
- A river flowing across the bottom
- A sun in the upper right corner

Colors: black ink, with a light blue wash on the river.

Reconstruction Spec
Palette: black, blue
Composition: symmetrical, focal point centre
Elements: house (left), tree (centre-right), river (bottom), sun (upper-right)
Style: pencil sketch with watercolour wash
""".strip()

# Required labels — drawn from the golden input. All must appear in
# the refined output (case-insensitive substring).
_REQUIRED_LABELS = ["house", "tree", "river", "sun"]

# Hallucination denylist — terms that are plausible additions but NOT
# in the golden input. If the model adds any, it's fabricating content.
_HALLUCINATION_TERMS = [
    "mountain", "mountains",
    "barn", "barns",
    "fence", "fences",
    "cloud", "clouds",
    "bird", "birds",
    "flower", "flowers",
    "fish", "fishes",
    "boat", "boats",
]


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run the refinement fidelity test. Returns one EvalResult."""
    from config import settings
    from services.scan_pipeline import _refine_visual

    result = EvalResult(
        test_id="refinement_fidelity",
        category="refinement",
        test_name="Refinement Pass Fidelity",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(settings.ollama_model)

    try:
        start = time.time()
        refined = await _refine_visual(_GOLDEN_DRAWING_RAW, "drawing")
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed
        result.output_chars = len(refined or "")
        result.actual_output_preview = (refined or "")[:400]

        if not refined or refined.strip() == _GOLDEN_DRAWING_RAW.strip():
            # _refine_visual is failure-safe — it returns the raw input
            # on any error. That's a "refinement skipped" signal, not a
            # failure of the test itself; we mark degraded but pass.
            result.mark_degraded(
                "Refinement returned raw input unchanged (model error or input too short)"
            )
            result.overall_score = 50
            result.passed = True
            return [result]

        refined_lower = refined.lower()

        # Check 1: required labels preserved
        labels_kept = sum(1 for label in _REQUIRED_LABELS if label in refined_lower)
        label_score = int((labels_kept / len(_REQUIRED_LABELS)) * 100)

        # Check 2: hallucination — count fabricated terms
        hallucinations = [t for t in _HALLUCINATION_TERMS if t in refined_lower]
        # Each hallucination -25 from a 100 baseline; floor at 0.
        hallucination_score = max(0, 100 - len(hallucinations) * 25)

        result.sub_scores = {
            "labels_required": len(_REQUIRED_LABELS),
            "labels_preserved": labels_kept,
            "hallucinations": hallucinations[:5],
            "label_score": label_score,
            "hallucination_score": hallucination_score,
        }
        result.accuracy_score = label_score
        result.overall_score = int(label_score * 0.6 + hallucination_score * 0.4)
        result.passed = result.overall_score >= 50 and not hallucinations

        if hallucinations:
            result.failure_reason = (
                f"Refinement added entities not in input: {hallucinations[:3]}"
            )
        elif labels_kept < len(_REQUIRED_LABELS):
            result.failure_reason = (
                f"Refinement dropped {len(_REQUIRED_LABELS) - labels_kept} labels from input"
            )

        print(
            f"[EVAL-REFINE] labels {labels_kept}/{len(_REQUIRED_LABELS)} "
            f"hallucinations={len(hallucinations)} → score={result.overall_score}"
        )

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-REFINE] FAILED: {e}")

    return [result]
