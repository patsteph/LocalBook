"""Visual System v2 — vision-based critic with adaptive retry.

Renders the freeform SVG output to a PNG, feeds it to a multimodal model
(Gemma for Setup B, granite-vision for Setup A), and scores it on 5 axes
matching the spike-validated approach:

  legibility • hierarchy • balance • color_harmony • message_clarity

When the overall score is below threshold (default 0.70), emits a diff
hint — strengths + weaknesses + suggestions verbatim — that the freeform
generator consumes for a single retry. Max 1 retry per visual.

Critic latency from spike: ~14s per visual on Gemma. Negligible vs the
~2-3 minute generation cost.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from services.ollama_service import ollama_service
from services.visual_capability import VisualCapability, get_capability

logger = logging.getLogger(__name__)


DEFAULT_THRESHOLD = 0.70
# Granite vision is less capable than Gemma vision; expect noisier scores
# and lower absolute values. Threshold scales to model family in
# threshold_for_model() so retry logic still does the right thing on Setup A.
GRANITE_THRESHOLD = 0.55
CRITIC_NUM_PREDICT = 1500
CRITIC_TIMEOUT = 180.0


def threshold_for_model(critic_model: Optional[str]) -> float:
    """Return the adaptive critic threshold for the model family in use."""
    if not critic_model:
        return DEFAULT_THRESHOLD
    name = critic_model.lower()
    if name.startswith("granite"):
        return GRANITE_THRESHOLD
    return DEFAULT_THRESHOLD


CRITIC_SYSTEM = """You are a senior information designer reviewing a visual produced for an enterprise customer presentation. Your job: score it on 5 axes and provide actionable critique.

You will see a single rendered image. Score each axis from 0.0 to 1.0:
- 0.0-0.3 = unusable, would embarrass the presenter
- 0.4-0.6 = workable but visibly amateur
- 0.7-0.8 = solid professional quality
- 0.9-1.0 = polished, conference-keynote quality

SCORING AXES:
1. legibility — every label is readable; no overlap, no microscopic text, sufficient contrast
2. hierarchy — title/section/body distinguishable; eye knows where to land first; focal point is clear
3. balance — whitespace is intentional; elements feel grouped not cramped; composition feels stable
4. color_harmony — palette is coherent; no clashing colors; accent use is restrained
5. message_clarity — the visual communicates its intent without squinting; relationships are obvious

Also return:
- overall: weighted average (you decide the weighting based on what matters most for this visual type)
- strengths: 2-3 specific things this visual does well
- weaknesses: 2-3 specific issues that would block it from a customer presentation
- suggestions: 2-3 concrete, actionable fixes (not vague advice)

Be honest. Real customer-presentation work doesn't score 0.9+ on first pass. A 0.70 is "good enough to ship"; under 0.70 means retry.

Return ONLY valid JSON matching this schema:
{
  "legibility": 0.0, "hierarchy": 0.0, "balance": 0.0,
  "color_harmony": 0.0, "message_clarity": 0.0, "overall": 0.0,
  "strengths": ["..."], "weaknesses": ["..."], "suggestions": ["..."]
}"""


# ──────────────────────────────────────────────────────────────────────
# Result type
# ──────────────────────────────────────────────────────────────────────
@dataclass
class CritiqueResult:
    success: bool
    legibility: float = 0.0
    hierarchy: float = 0.0
    balance: float = 0.0
    color_harmony: float = 0.0
    message_clarity: float = 0.0
    overall: float = 0.0
    strengths: list[str] = None
    weaknesses: list[str] = None
    suggestions: list[str] = None
    elapsed_ms: int = 0
    critic_model: Optional[str] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.strengths is None:
            self.strengths = []
        if self.weaknesses is None:
            self.weaknesses = []
        if self.suggestions is None:
            self.suggestions = []

    def passed(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.success and self.overall >= threshold

    def diff_hint(self) -> str:
        """Format weaknesses + suggestions for inclusion in a regen prompt."""
        parts = ["The previous version of this visual scored below threshold. "
                 "Fix these specific issues in the next attempt:\n"]
        if self.weaknesses:
            parts.append("WEAKNESSES TO FIX:")
            parts.extend(f"- {w}" for w in self.weaknesses)
            parts.append("")
        if self.suggestions:
            parts.append("CONCRETE CHANGES TO MAKE:")
            parts.extend(f"- {s}" for s in self.suggestions)
        return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────
# Critic
# ──────────────────────────────────────────────────────────────────────
class VisualCritic:
    """Vision-model critic that scores a rendered visual."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.threshold = threshold

    def _pick_critic_model(self, capability: VisualCapability) -> Optional[str]:
        """Setup B prefers Gemma itself (already multimodal); Setup A uses the
        configured vision model."""
        if capability.can_critic_gemma_vision and capability.gemma_model:
            return capability.gemma_model
        if capability.can_critic_separate_vision and capability.vision_model:
            return capability.vision_model
        return None

    async def critique(
        self,
        png_bytes: bytes,
        visual_title: str,
        visual_intent: str = "",
        capability: Optional[VisualCapability] = None,
    ) -> CritiqueResult:
        """Score a rendered visual. Returns CritiqueResult with axes + critique."""
        t0 = time.time()
        cap = capability or await get_capability()
        critic_model = self._pick_critic_model(cap)
        if not critic_model:
            return CritiqueResult(
                success=False,
                error="no critic model available (need Gemma or a vision model)",
                elapsed_ms=int((time.time() - t0) * 1000),
            )

        b64 = base64.b64encode(png_bytes).decode("ascii")
        intent_line = f"Intent: {visual_intent}\n" if visual_intent else ""
        prompt = (
            f"Visual title: {visual_title}\n"
            f"{intent_line}"
            f"This is an enterprise customer-presentation visual. "
            f"Score it on the 5 axes and return JSON only."
        )

        logger.info(f"[visual_critic] critic_model={critic_model}")
        result = await ollama_service.generate(
            prompt=prompt,
            system=CRITIC_SYSTEM,
            model=critic_model,
            temperature=0.2,
            num_predict=CRITIC_NUM_PREDICT,
            timeout=CRITIC_TIMEOUT,
            images=[b64],
            format="json",
            voice_modifier=False,
        )
        raw = result.get("response", "")
        parsed = _repair_json(raw)
        elapsed_ms = int((time.time() - t0) * 1000)

        if not parsed:
            return CritiqueResult(
                success=False,
                critic_model=critic_model,
                elapsed_ms=elapsed_ms,
                error="critic JSON parse failed",
            )

        # Detect the all-zeros silent-fail: when the LLM returned malformed
        # JSON that parsed as empty (or partial without numeric axes), each
        # axis clamps to 0.0. An all-zero score is meaningless — treat it
        # as a critic failure rather than displaying 0.00 to the user.
        axes = [
            _clamp(parsed.get("legibility")),
            _clamp(parsed.get("hierarchy")),
            _clamp(parsed.get("balance")),
            _clamp(parsed.get("color_harmony")),
            _clamp(parsed.get("message_clarity")),
        ]
        overall = _clamp(parsed.get("overall"))
        if overall == 0.0 and all(a == 0.0 for a in axes):
            return CritiqueResult(
                success=False,
                critic_model=critic_model,
                elapsed_ms=elapsed_ms,
                error="critic returned all-zero scores (likely JSON parse partial-fail)",
            )

        return CritiqueResult(
            success=True,
            legibility=axes[0],
            hierarchy=axes[1],
            balance=axes[2],
            color_harmony=axes[3],
            message_clarity=axes[4],
            overall=overall,
            strengths=list(parsed.get("strengths") or []),
            weaknesses=list(parsed.get("weaknesses") or []),
            suggestions=list(parsed.get("suggestions") or []),
            critic_model=critic_model,
            elapsed_ms=elapsed_ms,
        )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _repair_json(raw: str) -> Optional[dict]:
    """Strip code fences, then try strict parse, then outer-brace scrape."""
    if not raw:
        return None
    cleaned = _FENCE_RE.sub("", raw.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _clamp(v) -> float:
    """Coerce to float and clamp to [0, 1]; missing → 0.0."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f < 0:
        return 0.0
    if f > 1:
        return 1.0
    return f


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────
visual_critic = VisualCritic()
