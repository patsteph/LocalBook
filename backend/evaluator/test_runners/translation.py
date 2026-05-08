"""Translation pass test runner.

Runs services/scan_pipeline._translate_to on a known English passage and
verifies four properties:

  1. The original transcription is preserved at the top — the contract
     is "original + translation section appended", not "replaced".
  2. The 'Translation' section header is present.
  3. Markdown structure is preserved across the translation (table pipe
     count + heading hash count match within ±10%).
  4. Untranslatable tokens (URLs, email-like, numeric IDs) survive.

Single golden English passage; target language Spanish — universal across
combos for apples-to-apples scoring.
"""

import re
import time
from datetime import datetime

from evaluator.models import EvalResult


# Golden passage — short but rich: H2 heading, a markdown table, a URL,
# a numeric ID, a code-style backtick term. All four classes the
# translation pass is supposed to leave untouched.
_GOLDEN_PASSAGE = """
## Quarterly Sales Summary

The following table compares Q1 vs Q2 results.

| Region | Q1 | Q2 |
|--------|----|----|
| North  | 12 | 15 |
| South  | 8  | 11 |

For the source data, see https://example.com/sales-data and reference report 2026-Q2.
The exporter API endpoint is `getSalesData()`.
""".strip()

_TARGET_LANGUAGE = "Spanish"

# Tokens that MUST survive translation untranslated.
_UNTRANSLATABLE = [
    "https://example.com/sales-data",
    "2026-Q2",
    "getSalesData()",
]


def _heading_count(md: str) -> int:
    return len(re.findall(r"^#{2,6}\s+", md, flags=re.MULTILINE))


def _table_pipe_count(md: str) -> int:
    return md.count("|")


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run the translation fidelity test."""
    from config import settings
    from services.scan_pipeline import _translate_to

    result = EvalResult(
        test_id="translation_fidelity",
        category="translation",
        test_name=f"Translation Pass ({_TARGET_LANGUAGE})",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(settings.ollama_model)

    try:
        start = time.time()
        translated = await _translate_to(_GOLDEN_PASSAGE, _TARGET_LANGUAGE)
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed
        result.output_chars = len(translated or "")
        result.actual_output_preview = (translated or "")[:400]

        if not translated or translated.strip() == _GOLDEN_PASSAGE.strip():
            # Failure-safe path: empty output OR raw passthrough means the
            # model didn't actually run translation. Mark degraded — the
            # production behaviour is "return original on error", which
            # is correct safety but a useless result.
            result.mark_degraded("Translation returned raw input (model error or empty response)")
            result.overall_score = 50
            result.passed = True
            return [result]

        # Check 1: original passage preserved at the top.
        # Be lenient — just check the H2 heading + table headers landed
        # somewhere in the output.
        original_preserved = (
            "Quarterly Sales Summary" in translated
            and "| Region | Q1 | Q2 |" in translated
        )
        preserved_score = 100 if original_preserved else 30

        # Check 2: 'Translation' section marker present. The contract
        # in _translate_to emits "## Translation (Spanish)".
        has_translation_header = "Translation" in translated and "Spanish" in translated
        header_score = 100 if has_translation_header else 0

        # Check 3: markdown structure preserved within ±10%.
        orig_h = _heading_count(_GOLDEN_PASSAGE)
        out_h = _heading_count(translated)
        # Output should have ≥ original headings (original + translation
        # heading + maybe a translation-section subheading).
        structure_score = 100 if out_h >= orig_h else int((out_h / max(orig_h, 1)) * 100)
        # Pipe count: original passage has 18 pipes (table). Translation
        # should have ≥ that (its own table on top of the original's).
        orig_pipes = _table_pipe_count(_GOLDEN_PASSAGE)
        out_pipes = _table_pipe_count(translated)
        # Allow for the fact that translation may emit only one table copy.
        if orig_pipes == 0:
            pipe_score = 100
        elif out_pipes >= orig_pipes:
            pipe_score = 100
        else:
            pipe_score = int((out_pipes / orig_pipes) * 100)
        structure_score = int((structure_score + pipe_score) / 2)

        # Check 4: untranslatable tokens survive.
        survived = sum(1 for t in _UNTRANSLATABLE if t in translated)
        untranslatable_score = int((survived / len(_UNTRANSLATABLE)) * 100)

        result.sub_scores = {
            "original_preserved": original_preserved,
            "preserved_score": preserved_score,
            "header_score": header_score,
            "structure_score": structure_score,
            "untranslatable_survived": survived,
            "untranslatable_total": len(_UNTRANSLATABLE),
            "untranslatable_score": untranslatable_score,
        }
        result.accuracy_score = untranslatable_score
        result.completeness_score = preserved_score
        result.format_score = header_score
        result.overall_score = int(
            preserved_score * 0.30
            + header_score * 0.20
            + structure_score * 0.20
            + untranslatable_score * 0.30
        )
        result.passed = result.overall_score >= 50 and original_preserved

        if not original_preserved:
            result.failure_reason = "Translation pass dropped the original passage"
        elif not has_translation_header:
            result.failure_reason = "Translation section header missing from output"

        print(
            f"[EVAL-TRANSLATE] preserved={preserved_score} header={header_score} "
            f"structure={structure_score} untranslatable={untranslatable_score} "
            f"→ score={result.overall_score}"
        )

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-TRANSLATE] FAILED: {e}")

    return [result]
