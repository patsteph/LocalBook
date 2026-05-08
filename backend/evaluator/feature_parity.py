"""Feature parity synthesizer — the 'will this combo actually work in prod?' verdict.

The evaluator runs many tests; this module compresses the raw category
scores into a flat list of user-facing features with a pass/degraded/fail
verdict each, so a user swapping to (for example) Bonsai-8B immediately
sees:

    ✅ Chat & Streaming
    ✅ Document Generation
    ⚠️  Structured JSON (quiz)       — Bonsai's 1-bit weights produce
                                       inconsistent JSON; app will retry.
    ⚠️  Long-context retrieval        — Bonsai is 4K ctx; longer notebooks
                                       will be truncated.
    🚫 Vision analysis               — skipped (no vision model configured)
    ✅ Semantic search
    ✅ Podcast (TTS)

Design notes
------------
- Categories with score ≥ 70 → PASS
- Categories with score 40–69 → DEGRADED (works but noticeably weaker)
- Categories with score < 40 and not skipped → FAIL (will break for users)
- Skipped categories → NOT_APPLICABLE (feature is literally not part of
  this combo; do NOT confuse with fail)
- Any test with `sub_scores.degraded = True` bumps its category to at
  least DEGRADED even if the numeric score passes, so the UI flags that
  inputs had to be adapted (e.g. needle test trimmed to fit a 4K ctx).
"""

from __future__ import annotations

from typing import Iterable


# ─── Mapping: category → user-facing feature name ──────────────────────────

_CATEGORY_TO_FEATURE = {
    "ingestion": "Source ingestion",
    "rag_chat": "RAG chat (Q&A)",
    "streaming": "Live streaming chat",
    "fast_followup": "Conversational follow-up",
    "document_gen": "Document & brief generation",
    "structured_json": "Quiz / structured JSON",
    "intent_classify": "Agent intent routing",
    "embedding_quality": "Semantic search",
    "vision": "Image / vision analysis",
    "tts_audio": "Podcast (TTS)",
    "instruction_follow": "Prompt / format compliance",
    "concurrency": "Multiple users / concurrency",
    "needle_haystack": "Long-context retention",
    "prompt_safety": "Prompt injection robustness",
    # New categories (apples-to-apples + feature coverage):
    "voice_modifier": "Agent voice consistency",
    "capture_modes": "Multi-mode scan",
    "refinement": "Visual refinement pass",
    "translation": "Capture translation",
    "confidence": "Capture confidence calibration",
}


# ─── Verdict ───────────────────────────────────────────────────────────────

PASS = "pass"
DEGRADED = "degraded"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"


def _category_has_degraded_test(cat: dict) -> bool:
    """Return True if any test in `cat` was marked degraded."""
    for t in cat.get("tests", []) or []:
        sub = t.get("sub_scores") or {}
        if isinstance(sub, dict) and sub.get("degraded") is True:
            return True
    return False


def _verdict_for(cat: dict) -> str:
    if cat.get("skipped"):
        return NOT_APPLICABLE
    score = float(cat.get("score", 0) or 0)
    is_degraded_input = _category_has_degraded_test(cat)
    if score < 40:
        return FAIL
    if score < 70 or is_degraded_input:
        return DEGRADED
    return PASS


_VERDICT_ICON = {PASS: "✅", DEGRADED: "⚠️", FAIL: "🚫", NOT_APPLICABLE: "⊘"}


def synthesize(categories: dict) -> list[dict]:
    """Produce an ordered list of feature-parity entries.

    `categories` is ComboEvalSummary.categories (dict keyed by category key,
    values are CategoryResult.to_dict()).
    """
    out: list[dict] = []
    # Preserve a stable order that matches the user-flow narrative.
    for key, label in _CATEGORY_TO_FEATURE.items():
        cat = categories.get(key)
        if cat is None:
            continue
        verdict = _verdict_for(cat)
        note = ""
        if verdict == NOT_APPLICABLE:
            note = cat.get("skip_reason", "") or "Not configured for this combo"
        elif verdict == DEGRADED:
            # Pull the first degraded note if present
            for t in cat.get("tests", []) or []:
                sub = t.get("sub_scores") or {}
                notes = sub.get("degraded_notes") if isinstance(sub, dict) else None
                if isinstance(notes, list) and notes:
                    note = notes[0]
                    break
            if not note:
                note = f"Passable but weak (score {cat.get('score', 0):.0f}/100)"
        elif verdict == FAIL:
            # First test's failure reason is the most actionable signal
            for t in cat.get("tests", []) or []:
                if t.get("failure_reason"):
                    note = t["failure_reason"]
                    break
            if not note:
                note = f"Scored {cat.get('score', 0):.0f}/100 — expect user-visible failures"
        out.append({
            "category": key,
            "feature": label,
            "verdict": verdict,
            "icon": _VERDICT_ICON[verdict],
            "score": round(float(cat.get("score", 0) or 0), 1),
            "note": note,
        })
    return out


def rollup(parity: Iterable[dict]) -> dict:
    """Single-line 'production readiness' roll-up for the summary banner.

    Returns a dict with counts per verdict plus a computed headline:
      "ready" (no fails, ≤1 degraded), "viable" (≤2 fails), "risky" (≥3 fails).
    """
    counts = {PASS: 0, DEGRADED: 0, FAIL: 0, NOT_APPLICABLE: 0}
    for entry in parity:
        v = entry.get("verdict")
        if v in counts:
            counts[v] += 1
    if counts[FAIL] == 0 and counts[DEGRADED] <= 1:
        headline = "ready"
    elif counts[FAIL] <= 2:
        headline = "viable"
    else:
        headline = "risky"
    return {"counts": counts, "headline": headline}


# ─── Smoke tests ───────────────────────────────────────────────────────────

def _run_smoke_tests():
    # Build a plausible category snapshot (as ComboEvalSummary.to_dict emits)
    categories = {
        "rag_chat": {"score": 85.0, "tests": [{}], "skipped": False},
        "streaming": {"score": 60.0, "tests": [{}], "skipped": False},
        "structured_json": {
            "score": 30.0,
            "tests": [{"failure_reason": "JSON parse failed"}],
            "skipped": False,
        },
        "vision": {"score": 0.0, "tests": [{}], "skipped": True,
                   "skip_reason": "No vision model configured"},
        "needle_haystack": {
            "score": 75.0,
            "tests": [{"sub_scores": {"degraded": True,
                                      "degraded_notes": ["Trimmed to 4K"]}}],
            "skipped": False,
        },
    }
    parity = synthesize(categories)

    by_cat = {p["category"]: p for p in parity}
    assert by_cat["rag_chat"]["verdict"] == PASS
    assert by_cat["streaming"]["verdict"] == DEGRADED   # score 60 < 70
    assert by_cat["structured_json"]["verdict"] == FAIL
    assert by_cat["vision"]["verdict"] == NOT_APPLICABLE
    # Needle passes numerically but is marked degraded due to input trimming
    assert by_cat["needle_haystack"]["verdict"] == DEGRADED
    assert "4K" in by_cat["needle_haystack"]["note"]

    roll = rollup(parity)
    assert roll["counts"][FAIL] == 1
    assert roll["headline"] in ("viable", "ready", "risky")
    print("[evaluator.feature_parity] Smoke tests passed.")


if __name__ == "__main__":
    _run_smoke_tests()
